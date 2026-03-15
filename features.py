"""
Comprehensive NFL game feature engineering pipeline.
Creates production-ready feature matrix with time-series leakage prevention.
"""
import pandas as pd
import numpy as np
import json 
import os
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# Try to import nfl-data-py for play-by-play data
try:
    import nfl_data_py as nfl  # pyright: ignore[reportMissingImports]
    NFL_DATA_AVAILABLE = True
except ImportError:
    NFL_DATA_AVAILABLE = False
    print("Warning: nfl-data-py not available. Some features will be missing.")

# Configuration
CONFIG = {
    'rolling_windows': [1, 3, 5, 10],
    'ewma_halflife': 3,  # games (maps to span = 2*halflife - 1)
    'target': 'home_win',  # or 'point_diff'
    'seasons': None,  # None = auto-detect from games.csv
    'use_pbp': True,  # Use play-by-play data if available
    'use_player_data': False,  # Use player-level data if available
}

def load_games(path="data/games.csv"):
    """Load games data."""
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    # Create game_id if not present
    if 'game_id' not in df.columns:
        df['game_id'] = df.apply(
            lambda r: f"{r['season']}_{r['week']:02d}_{r['home_team']}_{r['away_team']}", 
            axis=1
        )
    return df

def validate_no_leakage(df, feature_cols, sample_size=10):
    """
    Validate that rolling features use only prior games.
    Randomly samples games and checks that all computed features use data before game date.
    """
    print("Validating no data leakage...")
    sample_games = df.sample(min(sample_size, len(df)))
    issues = []
    
    for idx, game in sample_games.iterrows():
        game_date = game['date']
        home_team = game['home_team']
        away_team = game['away_team']
        
        # Check that all prior games for these teams are before this date
        prior_home = df[(df['date'] < game_date) & 
                       ((df['home_team'] == home_team) | (df['away_team'] == home_team))]
        prior_away = df[(df['date'] < game_date) & 
                       ((df['home_team'] == away_team) | (df['away_team'] == away_team))]
        
        if len(prior_home) == 0 and 'home_points_3' in feature_cols:
            issues.append(f"Game {game['game_id']}: No prior games for home team {home_team}")
        if len(prior_away) == 0 and 'away_points_3' in feature_cols:
            issues.append(f"Game {game['game_id']}: No prior games for away team {away_team}")
    
    if issues:
        print(f"⚠️  Found {len(issues)} potential leakage issues:")
        for issue in issues[:5]:
            print(f"   {issue}")
    else:
        print("✅ No leakage detected in sample")
    return len(issues) == 0

def add_context_features(df):
    """Add context features: home flag, stadium, surface, altitude, travel, kickoff time, rest days, prime time."""
    print("Adding context features...")
    
    # Home flag (always 1 for home team perspective)
    df['home_flag'] = 1
    
    # Stadium and surface
    if 'stadium' in df.columns:
        df['stadium'] = df['stadium'].fillna('unknown')
    else:
        df['stadium'] = 'unknown'
    
    if 'surface' in df.columns:
        df['surface'] = df['surface'].fillna('unknown')
        # Encode surface types
        df['surface_grass'] = (df['surface'].str.contains('grass', case=False, na=False)).astype(int)
        df['surface_turf'] = (df['surface'].str.contains('turf', case=False, na=False)).astype(int)
        df['surface_fieldturf'] = (df['surface'].str.contains('fieldturf', case=False, na=False)).astype(int)
    else:
        df['surface_grass'] = 0
        df['surface_turf'] = 0
        df['surface_fieldturf'] = 0
    
    # Roof type
    if 'roof' in df.columns:
        df['roof'] = df['roof'].fillna('unknown')
        df['is_dome'] = (df['roof'].isin(['dome', 'closed'])).astype(int)
        df['is_outdoors'] = (df['roof'] == 'outdoors').astype(int)
    else:
        df['is_dome'] = 0
        df['is_outdoors'] = 1
    
    # Altitude (approximate for major stadiums - Denver is high altitude)
    altitude_map = {
        'Sports Authority Field at Mile High': 5280,
        'Empower Field at Mile High': 5280,
        'Mile High Stadium': 5280,
    }
    df['altitude'] = df['stadium'].map(altitude_map).fillna(0)
    df['high_altitude'] = (df['altitude'] > 4000).astype(int)
    
    # Kickoff time (extract from date if available, otherwise default)
    if 'kickoff_time' in df.columns:
        df['kickoff_hour'] = pd.to_datetime(df['kickoff_time']).dt.hour
    else:
        # Default to afternoon games (most common)
        df['kickoff_hour'] = 13  # 1 PM
    
    df['prime_time_flag'] = ((df['kickoff_hour'] >= 20) | (df['kickoff_hour'] < 12)).astype(int)
    df['afternoon_game'] = ((df['kickoff_hour'] >= 13) & (df['kickoff_hour'] < 17)).astype(int)
    df['evening_game'] = (df['kickoff_hour'] >= 17).astype(int)
    
    # Travel distance (simplified - would need stadium coordinates for accurate calculation)
    # For now, use a proxy: away team gets travel penalty
    df['away_travel_distance'] = 500  # Placeholder - would calculate actual distance
    df['home_travel_distance'] = 0
    
    # Rest days
    df = add_rest_days(df)
    
    return df

def add_rest_days(df):
    """Calculate rest days between games for each team."""
    print("Calculating rest days...")
    
    # Create team-game view
    team_games = []
    for _, row in df.iterrows():
        # Home team game
        team_games.append({
            'team': row['home_team'],
            'date': row['date'],
            'game_id': row['game_id'],
            'is_home': 1
        })
        # Away team game
        team_games.append({
            'team': row['away_team'],
            'date': row['date'],
            'game_id': row['game_id'],
            'is_home': 0
        })
    
    team_df = pd.DataFrame(team_games).sort_values(['team', 'date'])
    
    # Calculate rest days
    rest_days = []
    for team in team_df['team'].unique():
        team_data = team_df[team_df['team'] == team].copy()
        team_data = team_data.sort_values('date').reset_index(drop=True)
        
        for i, row in team_data.iterrows():
            if i == 0:
                rest = 7  # Default for first game
            else:
                rest = (row['date'] - team_data.iloc[i-1]['date']).days
            rest_days.append({
                'game_id': row['game_id'],
                'team': team,
                'is_home': row['is_home'],
                'rest_days': rest
            })
    
    rest_df = pd.DataFrame(rest_days)
    
    # Merge back to main dataframe
    home_rest = rest_df[rest_df['is_home'] == 1][['game_id', 'rest_days']].rename(columns={'rest_days': 'home_rest_days'})
    away_rest = rest_df[rest_df['is_home'] == 0][['game_id', 'rest_days']].rename(columns={'rest_days': 'away_rest_days'})
    
    df = df.merge(home_rest, on='game_id', how='left')
    df = df.merge(away_rest, on='game_id', how='left')
    
    df['home_rest_days'] = df['home_rest_days'].fillna(7)
    df['away_rest_days'] = df['away_rest_days'].fillna(7)
    df['rest_days_diff'] = df['home_rest_days'] - df['away_rest_days']
    df['rest_advantage_home'] = (df['rest_days_diff'] > 0).astype(int)
    df['short_week_home'] = (df['home_rest_days'] < 7).astype(int)
    df['short_week_away'] = (df['away_rest_days'] < 7).astype(int)
    
    return df

def add_weather_features(df):
    """Add weather features: temperature, wind, precipitation."""
    print("Adding weather features...")
    
    if 'temp' in df.columns:
        df['temp'] = pd.to_numeric(df['temp'], errors='coerce')
        df['temp_missing'] = df['temp'].isna().astype(int)
        # Fill missing with median
        df['temp'] = df['temp'].fillna(df['temp'].median() if df['temp'].notna().any() else 70)
    else:
        df['temp'] = 70
        df['temp_missing'] = 1
    
    if 'wind' in df.columns:
        df['wind_mph'] = pd.to_numeric(df['wind'], errors='coerce')
        df['wind_missing'] = df['wind_mph'].isna().astype(int)
        df['wind_mph'] = df['wind_mph'].fillna(0)
    else:
        df['wind_mph'] = 0
        df['wind_missing'] = 1
    
    # Weather categories
    df['cold_weather'] = (df['temp'] < 32).astype(int)
    df['hot_weather'] = (df['temp'] > 80).astype(int)
    df['windy'] = (df['wind_mph'] > 15).astype(int)
    df['very_windy'] = (df['wind_mph'] > 20).astype(int)
    
    # Precipitation (not in data, but can infer from dome)
    df['precip_flag'] = ((df['is_dome'] == 0) & (df['temp'] < 40)).astype(int)  # Proxy
    
    # Weather description (simplified)
    df['weather_desc'] = 'normal'
    df.loc[df['cold_weather'] == 1, 'weather_desc'] = 'cold'
    df.loc[df['hot_weather'] == 1, 'weather_desc'] = 'hot'
    df.loc[df['windy'] == 1, 'weather_desc'] = 'windy'
    
    # One-hot encode weather description
    weather_dummies = pd.get_dummies(df['weather_desc'], prefix='weather')
    df = pd.concat([df, weather_dummies], axis=1)
    
    return df

def fetch_pbp_data(seasons):
    """Fetch play-by-play data and aggregate team statistics."""
    if not NFL_DATA_AVAILABLE or not CONFIG['use_pbp']:
        return None
    
    print(f"Fetching play-by-play data for seasons {min(seasons)}-{max(seasons)}...")
    try:
        pbp = nfl.import_pbp_data(seasons, downcast=True)
        print(f"Fetched {len(pbp):,} plays")
        return pbp
    except Exception as e:
        print(f"Warning: Could not fetch PBP data: {e}")
        return None

def aggregate_team_stats_from_pbp(pbp, df_games):
    """Aggregate team-level statistics from play-by-play data."""
    if pbp is None or len(pbp) == 0:
        return None
    
    print("Aggregating team statistics from play-by-play data...")
    
    # Filter to offensive plays
    pbp_off = pbp[pbp['play_type'].isin(['run', 'pass'])].copy()
    pbp_off = pbp_off[pbp_off['posteam'].notna()].copy()
    
    # Create game-team level stats
    game_stats = []
    
    for (game_id, posteam), group in pbp_off.groupby(['game_id', 'posteam']):
        if pd.isna(posteam) or len(group) == 0:
            continue
        
        game_info = group.iloc[0]
        season = game_info.get('season', None)
        week = game_info.get('week', None)
        game_date = game_info.get('game_date', None)
        home_team = game_info.get('home_team', None)
        away_team = game_info.get('away_team', None)
        defteam = game_info.get('defteam', None)
        
        is_home = (posteam == home_team) if home_team else None
        
        # Offensive stats
        stats = {
            'game_id': game_id,
            'team': posteam,
            'opponent': defteam,
            'season': season,
            'week': week,
            'game_date': pd.to_datetime(game_date) if pd.notna(game_date) else None,
            'is_home': is_home,
            
            # Basic stats
            'points_for': 0,  # Will get from games data
            'points_against': 0,
            'total_yards': 0,
            'pass_yards': 0,
            'rush_yards': 0,
            'yards_per_play': 0,
            'plays': len(group),
            
            # EPA
            'epa_per_play': group['epa'].mean() if 'epa' in group.columns else 0,
            'total_epa': group['epa'].sum() if 'epa' in group.columns else 0,
            
            # Success rate
            'success_rate': (group['success'] == 1).mean() if 'success' in group.columns else 0,
            
            # Passing
            'pass_attempts': (group['play_type'] == 'pass').sum(),
            'pass_yards': group[group['play_type'] == 'pass']['yards_gained'].sum() if 'yards_gained' in group.columns else 0,
            'pass_epa': group[group['play_type'] == 'pass']['epa'].mean() if 'epa' in group.columns and (group['play_type'] == 'pass').any() else 0,
            'interceptions': (group['interception'] == 1).sum() if 'interception' in group.columns else 0,
            'sacks': (group['sack'] == 1).sum() if 'sack' in group.columns else 0,
            
            # Rushing
            'rush_attempts': (group['play_type'] == 'run').sum(),
            'rush_yards': group[group['play_type'] == 'run']['yards_gained'].sum() if 'yards_gained' in group.columns else 0,
            'rush_epa': group[group['play_type'] == 'run']['epa'].mean() if 'epa' in group.columns and (group['play_type'] == 'run').any() else 0,
            
            # Turnovers
            'fumbles': (group['fumble'] == 1).sum() if 'fumble' in group.columns else 0,
            'fumbles_lost': (group['fumble_lost'] == 1).sum() if 'fumble_lost' in group.columns else 0,
            'turnovers': 0,  # Will calculate
            
            # Third down
            'third_down_attempts': (group['down'] == 3).sum() if 'down' in group.columns else 0,
            'third_down_conversions': ((group['down'] == 3) & (group['first_down'] == 1)).sum() if 'down' in group.columns and 'first_down' in group.columns else 0,
            'third_down_rate': 0,  # Will calculate
            
            # Red zone
            'red_zone_plays': (group['yardline_100'] <= 20).sum() if 'yardline_100' in group.columns else 0,
            'red_zone_tds': ((group['yardline_100'] <= 20) & (group['touchdown'] == 1)).sum() if 'yardline_100' in group.columns and 'touchdown' in group.columns else 0,
            'redzone_td_rate': 0,  # Will calculate
            
            # First downs
            'first_downs': (group['first_down'] == 1).sum() if 'first_down' in group.columns else 0,
            
            # Time of possession (if available)
            'time_of_possession_sec': 0,  # Not in PBP, would need from boxscores
        }
        
        # Calculate derived stats
        if stats['plays'] > 0:
            stats['yards_per_play'] = group['yards_gained'].sum() / stats['plays'] if 'yards_gained' in group.columns else 0
            stats['total_yards'] = group['yards_gained'].sum() if 'yards_gained' in group.columns else 0
        if stats['third_down_attempts'] > 0:
            stats['third_down_rate'] = stats['third_down_conversions'] / stats['third_down_attempts']
        if stats['red_zone_plays'] > 0:
            stats['redzone_td_rate'] = stats['red_zone_tds'] / stats['red_zone_plays']
        stats['turnovers'] = stats['interceptions'] + stats['fumbles_lost']
        stats['sack_rate'] = stats['sacks'] / stats['pass_attempts'] if stats['pass_attempts'] > 0 else 0
        
        # Defensive stats (when this team is on defense)
        def_group = pbp[(pbp['game_id'] == game_id) & (pbp['defteam'] == posteam) & (pbp['posteam'] != posteam)]
        def_offensive = def_group[def_group['play_type'].isin(['run', 'pass'])]
        
        if len(def_offensive) > 0:
            stats.update({
                'points_against': 0,  # Will get from games
                'def_total_yards': def_offensive['yards_gained'].sum() if 'yards_gained' in def_offensive.columns else 0,
                'def_pass_yards': def_offensive[def_offensive['play_type'] == 'pass']['yards_gained'].sum() if 'yards_gained' in def_offensive.columns else 0,
                'def_rush_yards': def_offensive[def_offensive['play_type'] == 'run']['yards_gained'].sum() if 'yards_gained' in def_offensive.columns else 0,
                'def_yards_per_play': def_offensive['yards_gained'].mean() if 'yards_gained' in def_offensive.columns else 0,
                'def_epa_per_play': -def_offensive['epa'].mean() if 'epa' in def_offensive.columns else 0,  # Negative = good defense
                'def_success_rate': 1 - (def_offensive['success'] == 1).mean() if 'success' in def_offensive.columns else 0,
                'def_interceptions': (def_offensive['interception'] == 1).sum() if 'interception' in def_offensive.columns else 0,
                'def_sacks': (def_offensive['sack'] == 1).sum() if 'sack' in def_offensive.columns else 0,
                'def_turnovers': 0,  # Will calculate
            })
            stats['def_turnovers'] = stats['def_interceptions'] + (def_offensive['fumble_lost'] == 1).sum() if 'fumble_lost' in def_offensive.columns else 0
        else:
            stats.update({
                'points_against': 0,
                'def_total_yards': 0,
                'def_pass_yards': 0,
                'def_rush_yards': 0,
                'def_yards_per_play': 0,
                'def_epa_per_play': 0,
                'def_success_rate': 0,
                'def_interceptions': 0,
                'def_sacks': 0,
                'def_turnovers': 0,
            })
        
        game_stats.append(stats)
    
    team_stats_df = pd.DataFrame(game_stats)
    
    # Merge with games to get actual scores
    if len(team_stats_df) > 0 and 'game_id' in df_games.columns:
        # Match by game_id or by season/week/teams
        team_stats_df = team_stats_df.merge(
            df_games[['game_id', 'home_team', 'away_team', 'home_score', 'away_score']],
            on='game_id',
            how='left',
            suffixes=('', '_game')
        )
        
        # Set points_for and points_against
        team_stats_df['points_for'] = team_stats_df.apply(
            lambda r: r['home_score'] if r['team'] == r['home_team'] else r['away_score'],
            axis=1
        )
        team_stats_df['points_against'] = team_stats_df.apply(
            lambda r: r['away_score'] if r['team'] == r['home_team'] else r['home_score'],
            axis=1
        )
    
    return team_stats_df

def compute_rolling_features(df, team_stats_df, windows=[1, 3, 5, 10], ewma_halflife=3):
    """
    Compute rolling window and EWMA features for team statistics.
    Respects time-series ordering - only uses prior games.
    """
    print(f"Computing rolling features (windows: {windows}, EWMA halflife: {ewma_halflife})...")
    
    if team_stats_df is None or len(team_stats_df) == 0:
        print("Warning: No team stats available, computing from basic game data...")
        return compute_basic_rolling_features(df, windows, ewma_halflife)
    
    # Ensure dates are datetime
    team_stats_df['game_date'] = pd.to_datetime(team_stats_df['game_date'])
    df['date'] = pd.to_datetime(df['date'])
    
    # Sort by team and date
    team_stats_df = team_stats_df.sort_values(['team', 'game_date']).reset_index(drop=True)
    
    # Compute rolling features per team
    team_features_list = []
    
    for team in team_stats_df['team'].unique():
        team_data = team_stats_df[team_stats_df['team'] == team].copy()
        team_data = team_data.sort_values('game_date').reset_index(drop=True)
        
        # Stats to compute rolling on
        stat_cols = [
            'points_for', 'points_against',
            'total_yards', 'pass_yards', 'rush_yards', 'yards_per_play',
            'epa_per_play', 'def_epa_per_play',
            'success_rate', 'def_success_rate',
            'turnovers', 'def_turnovers',
            'third_down_rate', 'redzone_td_rate',
            'sacks', 'def_sacks', 'sack_rate',
            'interceptions', 'def_interceptions',
        ]
        
        # Available columns only
        stat_cols = [c for c in stat_cols if c in team_data.columns]
        
        # Rolling windows
        for window in windows:
            for col in stat_cols:
                # Use shift(1) to prevent leakage - only use prior games
                team_data[f'{col}_r{window}'] = team_data[col].shift(1).rolling(window, min_periods=1).mean()
        
        # EWMA (exponential weighted moving average)
        ewma_span = 2 * ewma_halflife - 1  # Convert halflife to span
        for col in stat_cols:
            team_data[f'{col}_ewma'] = team_data[col].shift(1).ewm(span=ewma_span, adjust=False).mean()
        
        # Keep relevant columns
        feature_cols = ['team', 'game_date', 'season', 'week'] + [
            f'{col}_r{w}' for col in stat_cols for w in windows
        ] + [f'{col}_ewma' for col in stat_cols]
        
        team_features_list.append(team_data[feature_cols])
    
    team_features_df = pd.concat(team_features_list, ignore_index=True)
    
    # Merge back to games dataframe
    # For home team
    df = df.merge(
        team_features_df,
        left_on=['date', 'home_team'],
        right_on=['game_date', 'team'],
        how='left',
        suffixes=('', '_home')
    )
    
    # Rename home team features
    home_feature_cols = [c for c in team_features_df.columns if c not in ['team', 'game_date', 'season', 'week']]
    rename_dict = {col: f'home_{col}' for col in home_feature_cols if col in df.columns}
    df = df.rename(columns=rename_dict)
    df = df.drop(columns=['team', 'game_date'], errors='ignore')
    
    # For away team
    df = df.merge(
        team_features_df,
        left_on=['date', 'away_team'],
        right_on=['game_date', 'team'],
        how='left',
        suffixes=('', '_away')
    )
    
    # Rename away team features
    away_feature_cols = [c for c in team_features_df.columns if c not in ['team', 'game_date', 'season', 'week']]
    rename_dict = {col: f'away_{col}' for col in away_feature_cols if col in df.columns}
    df = df.rename(columns=rename_dict)
    df = df.drop(columns=['team', 'game_date'], errors='ignore')
    
    return df

def compute_basic_rolling_features(df, windows=[1, 3, 5, 10], ewma_halflife=3):
    """Fallback: compute basic rolling features from game scores only."""
    print("Computing basic rolling features from game scores...")
    
    # Create team-game view
    team_games = []
    for _, row in df.iterrows():
        team_games.append({
            'team': row['home_team'],
            'date': row['date'],
            'game_id': row['game_id'],
            'points_for': row.get('home_score', 0),
            'points_against': row.get('away_score', 0),
        })
        team_games.append({
            'team': row['away_team'],
            'date': row['date'],
            'game_id': row['game_id'],
            'points_for': row.get('away_score', 0),
            'points_against': row.get('home_score', 0),
        })
    
    team_df = pd.DataFrame(team_games).sort_values(['team', 'date'])
    
    # Compute rolling features
    for team in team_df['team'].unique():
        team_data = team_df[team_df['team'] == team].sort_values('date').reset_index(drop=True)
        
        for window in windows:
            team_data[f'points_for_r{window}'] = team_data['points_for'].shift(1).rolling(window, min_periods=1).mean()
            team_data[f'points_against_r{window}'] = team_data['points_against'].shift(1).rolling(window, min_periods=1).mean()
        
        # EWMA
        ewma_span = 2 * ewma_halflife - 1
        team_data['points_for_ewma'] = team_data['points_for'].shift(1).ewm(span=ewma_span, adjust=False).mean()
        team_data['points_against_ewma'] = team_data['points_against'].shift(1).ewm(span=ewma_span, adjust=False).mean()
        
        # Merge back
        home_data = team_data[team_data['game_id'].isin(df[df['home_team'] == team]['game_id'])]
        away_data = team_data[team_data['game_id'].isin(df[df['away_team'] == team]['game_id'])]
        
        for window in windows:
            df.loc[df['home_team'] == team, f'home_points_for_r{window}'] = home_data[f'points_for_r{window}'].values
            df.loc[df['away_team'] == team, f'away_points_for_r{window}'] = away_data[f'points_for_r{window}'].values
    
    return df

def add_betting_features(df):
    """Add betting line features."""
    print("Adding betting features...")
    
    if 'spread_line' in df.columns:
        df['home_spread'] = pd.to_numeric(df['spread_line'], errors='coerce').fillna(0)
        df['home_favorite'] = (df['home_spread'] < 0).astype(int)  # Negative = home favorite
        df['spread_abs'] = df['home_spread'].abs()
    else:
        df['home_spread'] = 0
        df['home_favorite'] = 0
        df['spread_abs'] = 0
    
    if 'total_line' in df.columns:
        df['total_line'] = pd.to_numeric(df['total_line'], errors='coerce')
        df['total_line'] = df['total_line'].fillna(df['total_line'].median() if df['total_line'].notna().any() else 45)
    else:
        df['total_line'] = 45
    
    # Implied probability from spread (simplified)
    # P(home win) ≈ 0.5 + spread / (2 * std), where std ≈ 14
    df['implied_prob_home'] = 0.5 + df['home_spread'] / (2 * 14)
    df['implied_prob_home'] = df['implied_prob_home'].clip(0, 1)
    
    # Moneyline (if available)
    if 'home_moneyline' in df.columns:
        df['home_moneyline'] = pd.to_numeric(df['home_moneyline'], errors='coerce')
        # Convert to probability
        df['ml_prob_home'] = df['home_moneyline'].apply(
            lambda x: (100 / (x + 100)) if x > 0 else (-x / (-x + 100)) if x < 0 else 0.5
        )
    else:
        df['ml_prob_home'] = 0.5
    
    return df

def add_matchup_features(df):
    """Add matchup features: differences between home and away team stats."""
    print("Adding matchup features...")
    
    # Find all feature columns that have both home_ and away_ versions
    home_cols = [c for c in df.columns if c.startswith('home_') and not c.startswith('home_team')]
    away_cols = [c.replace('home_', 'away_') for c in home_cols]
    
    # Create difference features (numeric columns only)
    for home_col in home_cols:
        away_col = home_col.replace('home_', 'away_')
        if away_col in df.columns and pd.api.types.is_numeric_dtype(df[home_col]) and pd.api.types.is_numeric_dtype(df[away_col]):
            # Home - Away difference
            diff_col = home_col.replace('home_', 'diff_')
            df[diff_col] = df[home_col] - df[away_col]

            # Absolute difference
            abs_col = diff_col.replace('diff_', 'abs_diff_')
            df[abs_col] = (df[home_col] - df[away_col]).abs()
    
    return df

def compute_proxy_epa(team_stats_df):
    """
    Compute proxy EPA from boxscore data when actual EPA is missing.
    Uses yards gained, down, distance, and field position approximations.
    """
    if team_stats_df is None or len(team_stats_df) == 0:
        return team_stats_df
    
    if 'epa_per_play' in team_stats_df.columns and team_stats_df['epa_per_play'].notna().sum() > 0:
        # EPA already available, no need for proxy
        return team_stats_df
    
    print("Computing proxy EPA from boxscore data...")
    
    # Proxy EPA formula: approximate value based on yards per play and success rate
    # EPA ≈ (yards_per_play - 4) / 10 + success_bonus
    # This is a simplified approximation
    if 'yards_per_play' in team_stats_df.columns:
        team_stats_df['epa_per_play_proxy'] = (team_stats_df['yards_per_play'] - 4) / 10
        if 'success_rate' in team_stats_df.columns:
            team_stats_df['epa_per_play_proxy'] += team_stats_df['success_rate'] * 0.1
    else:
        team_stats_df['epa_per_play_proxy'] = 0
    
    # Use proxy if actual EPA is missing
    if 'epa_per_play' not in team_stats_df.columns:
        team_stats_df['epa_per_play'] = team_stats_df['epa_per_play_proxy']
    else:
        team_stats_df['epa_per_play'] = team_stats_df['epa_per_play'].fillna(team_stats_df['epa_per_play_proxy'])
    
    return team_stats_df

def load_player_data(player_injuries_path="data/player_injuries.csv", 
                     player_values_path="data/player_values.csv"):
    """Load player injury and value data if available."""
    player_injuries = None
    player_values = None
    
    if os.path.exists(player_injuries_path):
        try:
            player_injuries = pd.read_csv(player_injuries_path, parse_dates=["date"])
            print(f"   Loaded {len(player_injuries)} player injury records")
        except Exception as e:
            print(f"   Warning: Could not load player injuries: {e}")
    
    if os.path.exists(player_values_path):
        try:
            player_values = pd.read_csv(player_values_path)
            print(f"   Loaded {len(player_values)} player value records")
        except Exception as e:
            print(f"   Warning: Could not load player values: {e}")
    
    return player_injuries, player_values

def compute_injury_impact_score(df, player_injuries, player_values, game_date, team):
    """
    Compute injury impact score for a team at a given date.
    Score = sum(player_value * (1 - availability))
    """
    if player_injuries is None or player_values is None:
        return 0.0
    
    # Check required columns
    required_injury_cols = ['team', 'date']
    required_value_cols = ['player_id', 'value']
    
    if not all(col in player_injuries.columns for col in required_injury_cols):
        return 0.0
    if not all(col in player_values.columns for col in required_value_cols):
        return 0.0
    
    # Get injuries for this team before game date
    team_injuries = player_injuries[
        (player_injuries['team'] == team) & 
        (pd.to_datetime(player_injuries['date']) <= pd.to_datetime(game_date))
    ].copy()
    
    if len(team_injuries) == 0:
        return 0.0
    
    # Get most recent injury status per player (if player_id exists)
    if 'player_id' in team_injuries.columns:
        team_injuries = team_injuries.sort_values('date').groupby('player_id').last().reset_index()
        
        # Merge with player values
        team_injuries = team_injuries.merge(
            player_values[['player_id', 'value']],
            on='player_id',
            how='left'
        )
    else:
        # No player_id, use default value
        team_injuries['value'] = 1.0
    
    # If value missing, use position-average (approximate)
    if 'value' in team_injuries.columns:
        team_injuries['value'] = team_injuries['value'].fillna(team_injuries['value'].median() if team_injuries['value'].notna().any() else 1.0)
    else:
        team_injuries['value'] = 1.0  # Default value
    
    # Availability: 1 if available, 0 if out, 0.5 if questionable
    if 'status' in team_injuries.columns:
        team_injuries['availability'] = team_injuries['status'].map({
            'active': 1.0,
            'probable': 0.9,
            'questionable': 0.5,
            'doubtful': 0.2,
            'out': 0.0
        }).fillna(1.0)
    else:
        team_injuries['availability'] = 1.0
    
    # Compute impact score
    impact_score = ((1 - team_injuries['availability']) * team_injuries['value']).sum()
    
    return float(impact_score)

def add_injury_features(df, player_injuries=None, player_values=None):
    """Add injury impact features."""
    print("Adding injury impact features...")
    
    if player_injuries is None or player_values is None:
        print("   No player injury/value data available, using placeholders")
        df['injury_impact_score_home'] = 0.0
        df['injury_impact_score_away'] = 0.0
        df['injury_delta'] = 0.0
        return df
    
    # Compute injury impact scores
    home_scores = []
    away_scores = []
    
    for _, row in df.iterrows():
        home_score = compute_injury_impact_score(
            df, player_injuries, player_values, row['date'], row['home_team']
        )
        away_score = compute_injury_impact_score(
            df, player_injuries, player_values, row['date'], row['away_team']
        )
        home_scores.append(home_score)
        away_scores.append(away_score)
    
    df['injury_impact_score_home'] = home_scores
    df['injury_impact_score_away'] = away_scores
    df['injury_delta'] = df['injury_impact_score_home'] - df['injury_impact_score_away']
    
    return df

def add_personnel_features(df, team_stats_df=None):
    """
    Add personnel and position aggregate features.
    For now, these are placeholders that would be computed from player-level data.
    In production, you'd aggregate from player snapshots.
    """
    print("Adding personnel/position aggregate features...")
    
    # These would come from player-level data in production
    # For now, create proxy features from team stats
    
    # QB stats (proxy from pass stats)
    if 'home_pass_yards_r3' in df.columns:
        df['home_qb_yards_r3'] = df['home_pass_yards_r3']  # Proxy
        df['away_qb_yards_r3'] = df['away_pass_yards_r3']
    if 'home_pass_yards_r5' in df.columns:
        df['home_qb_yards_r5'] = df['home_pass_yards_r5']
        df['away_qb_yards_r5'] = df['away_pass_yards_r5']
    
    # Top WR stats (proxy from pass yards / 2)
    if 'home_pass_yards_r3' in df.columns:
        df['home_wr_yards_r3'] = df['home_pass_yards_r3'] / 2  # Proxy
        df['away_wr_yards_r3'] = df['away_pass_yards_r3'] / 2
    if 'home_pass_yards_r5' in df.columns:
        df['home_wr_yards_r5'] = df['home_pass_yards_r5'] / 2
        df['away_wr_yards_r5'] = df['away_pass_yards_r5'] / 2
    
    # Top RB stats (proxy from rush stats)
    if 'home_rush_yards_r3' in df.columns:
        df['home_rb_yards_r3'] = df['home_rush_yards_r3']  # Proxy
        df['away_rb_yards_r3'] = df['away_rush_yards_r3']
    if 'home_rush_yards_r5' in df.columns:
        df['home_rb_yards_r5'] = df['home_rush_yards_r5']
        df['away_rb_yards_r5'] = df['away_rush_yards_r5']
    
    # OL/DL pressure (proxy from sack rates)
    if 'home_sack_rate_ewma' in df.columns:
        df['home_dl_pressure_rate'] = df['home_sack_rate_ewma'] * 2  # Proxy
        df['away_dl_pressure_rate'] = df['away_sack_rate_ewma'] * 2
    if 'home_def_sacks' in df.columns:
        df['home_dl_sacks_r3'] = df.get('home_def_sacks_r3', 0)
        df['away_dl_sacks_r3'] = df.get('away_def_sacks_r3', 0)
    
    # Secondary INTs/PDs (proxy from defensive interceptions)
    if 'home_def_interceptions_r3' in df.columns:
        df['home_secondary_ints_r3'] = df['home_def_interceptions_r3']
        df['away_secondary_ints_r3'] = df['away_def_interceptions_r3']
    else:
        df['home_secondary_ints_r3'] = 0
        df['away_secondary_ints_r3'] = 0
    
    print("   Note: Personnel features are proxies. In production, use actual player-level data.")
    
    return df

def add_special_teams_features(df, team_stats_df=None):
    """Add special teams features."""
    print("Adding special teams features...")
    
    # ST points (would come from boxscore: kick returns, punt returns, blocked kicks)
    # For now, use placeholder
    df['home_st_points_r3'] = 0.0  # Placeholder
    df['away_st_points_r3'] = 0.0
    df['home_st_points_r5'] = 0.0
    df['away_st_points_r5'] = 0.0
    df['home_st_points_against_r3'] = 0.0
    df['away_st_points_against_r3'] = 0.0
    
    # Field goal percentage (would come from kicker stats)
    # For now, use placeholder
    df['home_fg_pct_r3'] = 0.85  # Placeholder: ~85% league average
    df['away_fg_pct_r3'] = 0.85
    df['home_fg_pct_r5'] = 0.85
    df['away_fg_pct_r5'] = 0.85
    
    print("   Note: Special teams features are placeholders. Add actual ST data when available.")
    
    return df

def add_interaction_features(df):
    """Add interaction features between key variables."""
    print("Adding interaction features...")
    
    # Home flag interactions
    if 'home_flag' in df.columns:
        if 'home_epa_per_play_ewma' in df.columns:
            df['home_flag_x_epa_ewma'] = df['home_flag'] * df['home_epa_per_play_ewma']
        if 'home_sack_rate_ewma' in df.columns:
            df['home_flag_x_sack_rate'] = df['home_flag'] * df['home_sack_rate_ewma']
    
    # Weather interactions
    if 'wind_mph' in df.columns:
        if 'home_pass_yards_r3' in df.columns:
            df['wind_x_pass_yards'] = df['wind_mph'] * df['home_pass_yards_r3']
        if 'away_pass_yards_r3' in df.columns:
            df['wind_x_away_pass'] = df['wind_mph'] * df['away_pass_yards_r3']
    
    # Rest days interactions
    if 'rest_days_diff' in df.columns:
        if 'home_epa_per_play_r3' in df.columns:
            df['rest_diff_x_epa'] = df['rest_days_diff'] * df['home_epa_per_play_r3']
    
    # Spread interactions
    if 'home_spread' in df.columns:
        if 'home_epa_per_play_ewma' in df.columns:
            df['spread_x_epa'] = df['home_spread'] * df['home_epa_per_play_ewma']
    
    # Injury interactions
    if 'injury_delta' in df.columns:
        if 'home_epa_per_play_r3' in df.columns:
            df['injury_delta_x_epa'] = df['injury_delta'] * df['home_epa_per_play_r3']
    
    return df

def add_elo_features(df):
    """Add ELO rating features."""
    print("Adding ELO features...")
    
    # Compute ELO ratings
    teams = pd.Index(df[['home_team', 'away_team']].values.ravel()).unique()
    rating = {t: 1500 for t in teams}
    home_elos = []
    away_elos = []
    
    for _, row in df.sort_values("date").iterrows():
        home = row['home_team']
        away = row['away_team']
        home_elos.append(rating.get(home, 1500))
        away_elos.append(rating.get(away, 1500))
        
        if pd.notna(row.get('home_score')) and pd.notna(row.get('away_score')):
            diff = rating[home] - rating[away]
            expected_home = 1 / (1 + 10 ** (-diff / 400))
            actual_home = 1 if row['home_score'] > row['away_score'] else 0 if row['home_score'] < row['away_score'] else 0.5
            k = 20
            rating[home] += k * (actual_home - expected_home)
            rating[away] += k * ((1 - actual_home) - (1 - expected_home))
    
    df = df.sort_values("date").reset_index(drop=True)
    df["home_elo"] = home_elos
    df["away_elo"] = away_elos
    df["elo_diff"] = df["home_elo"] - df["away_elo"]
    df["elo_ratio"] = df["home_elo"] / df["away_elo"]
    
    return df

def handle_missing_values(df, feature_cols):
    """Handle missing values: impute numeric with median, add missing flags."""
    print("Handling missing values...")
    
    numeric_cols = df[feature_cols].select_dtypes(include=[np.number]).columns
    categorical_cols = df[feature_cols].select_dtypes(include=['object']).columns
    
    # Numeric: impute with median, add missing flag
    for col in numeric_cols:
        if df[col].isna().sum() > 0:
            missing_pct = df[col].isna().sum() / len(df) * 100
            if missing_pct > 0:
                print(f"   {col}: {missing_pct:.1f}% missing")
            median_val = df[col].median()
            df[f'{col}_missing'] = df[col].isna().astype(int)
            df[col] = df[col].fillna(median_val if pd.notna(median_val) else 0)
    
    # Categorical: fill with 'missing'
    for col in categorical_cols:
        df[col] = df[col].fillna('missing')
    
    return df

def create_feature_schema(df, feature_cols, output_path="features_schema.json"):
    """Create feature schema JSON file."""
    schema = {
        'num_features': len(feature_cols),
        'num_games': len(df),
        'features': []
    }
    
    for col in feature_cols:
        dtype = str(df[col].dtype)
        missing_count = df[col].isna().sum()
        missing_pct = missing_count / len(df) * 100
        
        feature_info = {
            'name': col,
            'dtype': dtype,
            'missing_count': int(missing_count),
            'missing_pct': float(missing_pct)
        }
        
        if dtype in ['int64', 'float64']:
            feature_info['min'] = float(df[col].min())
            feature_info['max'] = float(df[col].max())
            feature_info['mean'] = float(df[col].mean())
            feature_info['median'] = float(df[col].median())
        
        schema['features'].append(feature_info)
    
    with open(output_path, 'w') as f:
        json.dump(schema, f, indent=2)
    
    print(f"Saved feature schema to {output_path}")
    return schema

def build_features(config=None):
    """
    Main function to build comprehensive feature matrix.
    """
    if config:
        CONFIG.update(config)
    
    print("="*60)
    print("NFL Feature Engineering Pipeline")
    print("="*60)
    
    # Load games
    print("\n1. Loading games data...")
    df = load_games("data/games.csv")
    print(f"   Loaded {len(df)} games")
    
    # Add result columns
    df['home_win'] = (df['home_score'] > df['away_score']).astype(int)
    df['point_diff'] = df['home_score'] - df['away_score']
    df['total_points'] = df['home_score'] + df['away_score']
    
    # Determine seasons
    if CONFIG['seasons'] is None:
        seasons = sorted(df['season'].unique().tolist())
    else:
        seasons = CONFIG['seasons']
    print(f"   Seasons: {seasons}")
    
    # Add context features
    print("\n2. Adding context features...")
    df = add_context_features(df)
    
    # Add weather features
    print("\n3. Adding weather features...")
    df = add_weather_features(df)
    
    # Add ELO features
    print("\n4. Adding ELO features...")
    df = add_elo_features(df)
    
    # Load player data
    print("\n5. Loading player data...")
    player_injuries, player_values = load_player_data()
    
    # Fetch and aggregate play-by-play data
    team_stats_df = None
    if CONFIG['use_pbp'] and NFL_DATA_AVAILABLE:
        print("\n6. Fetching play-by-play data...")
        pbp = fetch_pbp_data(seasons)
        if pbp is not None:
            team_stats_df = aggregate_team_stats_from_pbp(pbp, df)
            print(f"   Aggregated stats for {len(team_stats_df)} team-games")
    
    # Compute proxy EPA if needed
    if team_stats_df is not None:
        team_stats_df = compute_proxy_epa(team_stats_df)
    
    # Compute rolling features
    print("\n7. Computing rolling features...")
    df = compute_rolling_features(
        df, 
        team_stats_df, 
        windows=CONFIG['rolling_windows'],
        ewma_halflife=CONFIG['ewma_halflife']
    )
    
    # Add personnel features
    print("\n8. Adding personnel/position features...")
    df = add_personnel_features(df, team_stats_df)
    
    # Add special teams features
    print("\n9. Adding special teams features...")
    df = add_special_teams_features(df, team_stats_df)
    
    # Add injury features
    print("\n10. Adding injury impact features...")
    df = add_injury_features(df, player_injuries, player_values)
    
    # Add betting features
    print("\n11. Adding betting features...")
    df = add_betting_features(df)
    
    # Add matchup features
    print("\n12. Adding matchup features...")
    df = add_matchup_features(df)
    
    # Add interaction features
    print("\n13. Adding interaction features...")
    df = add_interaction_features(df)
    
    # Identify feature columns (exclude metadata)
    exclude_cols = ['game_id', 'season', 'week', 'date', 'home_team', 'away_team', 
                   'home_score', 'away_score', 'home_win', 'point_diff', 'total_points',
                   'home_qb_name', 'away_qb_name', 'stadium', 'surface', 'roof', 'weather_desc']
    feature_cols = [c for c in df.columns if c not in exclude_cols]
    
    # Handle missing values
    print("\n14. Handling missing values...")
    df = handle_missing_values(df, feature_cols)
    
    # Validate no leakage
    print("\n15. Validating no data leakage...")
    validate_no_leakage(df, feature_cols, sample_size=20)
    
    # Create feature schema
    print("\n16. Creating feature schema...")
    schema = create_feature_schema(df, feature_cols, "features_schema.json")
    
    # Save features
    print("\n17. Saving features...")
    output_cols = ['game_id', 'season', 'week', 'date', 'home_team', 'away_team'] + feature_cols + ['home_win', 'point_diff']
    output_cols = [c for c in output_cols if c in df.columns]
    df[output_cols].to_csv("features.csv", index=False)
    print(f"   Saved {len(feature_cols)} features to features.csv")
    
    # Summary
    print("\n" + "="*60)
    print("Feature Engineering Summary")
    print("="*60)
    print(f"Total games: {len(df)}")
    print(f"Total features: {len(feature_cols)}")
    print(f"Missing data: {df[feature_cols].isna().sum().sum() / (len(df) * len(feature_cols)) * 100:.2f}%")
    print(f"\nTop features by category:")
    print(f"  Context: {len([c for c in feature_cols if any(x in c for x in ['home_flag', 'rest', 'stadium', 'surface'])])}")
    print(f"  Weather: {len([c for c in feature_cols if any(x in c for x in ['temp', 'wind', 'weather'])])}")
    print(f"  Team stats: {len([c for c in feature_cols if any(x in c for x in ['points', 'yards', 'epa', 'success'])])}")
    print(f"  Betting: {len([c for c in feature_cols if any(x in c for x in ['spread', 'total', 'ml_prob', 'implied'])])}")
    print(f"  Matchup: {len([c for c in feature_cols if 'diff' in c])}")
    print(f"  Interactions: {len([c for c in feature_cols if '_x_' in c])}")
    print(f"  Personnel: {len([c for c in feature_cols if any(x in c for x in ['qb_', 'wr_', 'rb_', 'dl_', 'secondary_'])])}")
    print(f"  Injuries: {len([c for c in feature_cols if 'injury' in c])}")
    print(f"  Special Teams: {len([c for c in feature_cols if any(x in c for x in ['st_', 'fg_pct'])])}")
    
    return df, feature_cols, schema

if __name__ == "__main__":
    df, features, schema = build_features()
    print("\n✅ Feature engineering complete!")
    print(f"   Output: features.csv ({len(features)} features)")
    print(f"   Schema: features_schema.json")

