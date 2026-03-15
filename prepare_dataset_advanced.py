"""
Advanced dataset preparation using player-level stats from raw_weekly.csv.
Engineers team-level aggregates and QB-specific features with rolling windows.
Merges with featurized_games.csv to create featurized_games_advanced.csv.
"""
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

def load_data():
    """Load raw_weekly.csv and featurized_games.csv."""
    print("Loading data...")
    
    # Load player-level stats
    print("  Loading raw_weekly.csv...")
    raw_weekly = pd.read_csv("data/raw_weekly.csv")
    print(f"    Loaded {len(raw_weekly):,} player-week records")
    
    # Load existing featurized games
    print("  Loading featurized_games.csv...")
    featurized = pd.read_csv("data/featurized_games.csv", parse_dates=["date"])
    print(f"    Loaded {len(featurized):,} games")
    
    return raw_weekly, featurized

def aggregate_team_stats(raw_weekly):
    """
    Aggregate player-level stats to team-level per game.
    Returns team stats with offensive and defensive metrics.
    """
    print("\nAggregating player stats to team-level...")
    
    # Filter to regular season only
    raw_weekly = raw_weekly[raw_weekly['season_type'] == 'REG'].copy()
    
    # Group by team, season, week to get team offensive stats
    team_offense = raw_weekly.groupby(['recent_team', 'season', 'week']).agg({
        'passing_epa': 'sum',
        'rushing_epa': 'sum',
        'receiving_epa': 'sum',
        'passing_yards': 'sum',
        'rushing_yards': 'sum',
        'receiving_yards': 'sum',
        'attempts': 'sum',
        'completions': 'sum',
        'carries': 'sum',
        'sacks': 'sum',
        'interceptions': 'sum',
        'passing_first_downs': 'sum',
        'rushing_first_downs': 'sum',
        'receiving_first_downs': 'sum',
        'rushing_fumbles_lost': 'sum',
        'receiving_fumbles_lost': 'sum',
        'sack_fumbles_lost': 'sum',
        'passing_tds': 'sum',
        'rushing_tds': 'sum',
        'receiving_tds': 'sum',
    }).reset_index()
    
    # Calculate total EPA and plays
    team_offense['total_epa'] = (
        team_offense['passing_epa'].fillna(0) + 
        team_offense['rushing_epa'].fillna(0) + 
        team_offense['receiving_epa'].fillna(0)
    )
    
    team_offense['total_yards'] = (
        team_offense['passing_yards'].fillna(0) + 
        team_offense['rushing_yards'].fillna(0) + 
        team_offense['receiving_yards'].fillna(0)
    )
    
    team_offense['total_plays'] = (
        team_offense['attempts'].fillna(0) + 
        team_offense['carries'].fillna(0)
    )
    
    team_offense['total_first_downs'] = (
        team_offense['passing_first_downs'].fillna(0) + 
        team_offense['rushing_first_downs'].fillna(0) + 
        team_offense['receiving_first_downs'].fillna(0)
    )
    
    team_offense['total_turnovers'] = (
        team_offense['interceptions'].fillna(0) + 
        team_offense['rushing_fumbles_lost'].fillna(0) + 
        team_offense['receiving_fumbles_lost'].fillna(0) + 
        team_offense['sack_fumbles_lost'].fillna(0)
    )
    
    team_offense['total_tds'] = (
        team_offense['passing_tds'].fillna(0) + 
        team_offense['rushing_tds'].fillna(0) + 
        team_offense['receiving_tds'].fillna(0)
    )
    
    # Calculate rates
    team_offense['epa_per_play'] = team_offense['total_epa'] / team_offense['total_plays'].replace(0, np.nan)
    team_offense['yards_per_play'] = team_offense['total_yards'] / team_offense['total_plays'].replace(0, np.nan)
    team_offense['sacks_allowed'] = team_offense['sacks'].fillna(0)
    
    # Get defensive stats by looking at opponent stats
    # When team A plays team B, team A's defensive stats = team B's offensive stats
    # Aggregate by opponent_team to get defensive stats
    team_defense = raw_weekly.groupby(['opponent_team', 'season', 'week']).agg({
        'passing_epa': 'sum',
        'rushing_epa': 'sum',
        'receiving_epa': 'sum',
        'passing_yards': 'sum',
        'rushing_yards': 'sum',
        'receiving_yards': 'sum',
        'attempts': 'sum',
        'carries': 'sum',
        'sacks': 'sum',
        'interceptions': 'sum',
        'rushing_fumbles_lost': 'sum',
        'receiving_fumbles_lost': 'sum',
        'sack_fumbles_lost': 'sum',
    }).reset_index()
    
    # Rename opponent_team to recent_team for merging
    team_defense = team_defense.rename(columns={'opponent_team': 'recent_team'})
    
    # Calculate defensive totals
    team_defense['def_epa'] = (
        team_defense['passing_epa'].fillna(0) + 
        team_defense['rushing_epa'].fillna(0) + 
        team_defense['receiving_epa'].fillna(0)
    )
    team_defense['def_yards'] = (
        team_defense['passing_yards'].fillna(0) + 
        team_defense['rushing_yards'].fillna(0) + 
        team_defense['receiving_yards'].fillna(0)
    )
    team_defense['def_plays'] = (
        team_defense['attempts'].fillna(0) + 
        team_defense['carries'].fillna(0)
    )
    team_defense['def_turnovers'] = (
        team_defense['interceptions'].fillna(0) + 
        team_defense['rushing_fumbles_lost'].fillna(0) + 
        team_defense['receiving_fumbles_lost'].fillna(0) + 
        team_defense['sack_fumbles_lost'].fillna(0)
    )
    team_defense['def_sacks'] = team_defense['sacks'].fillna(0)
    
    # Calculate defensive rates
    team_defense['def_epa_per_play'] = team_defense['def_epa'] / team_defense['def_plays'].replace(0, np.nan)
    team_defense['def_yards_per_play'] = team_defense['def_yards'] / team_defense['def_plays'].replace(0, np.nan)
    
    # Merge offense and defense
    team_stats = team_offense.merge(
        team_defense[['recent_team', 'season', 'week', 'def_epa', 'def_yards', 'def_plays', 
                     'def_turnovers', 'def_sacks', 'def_epa_per_play', 'def_yards_per_play']],
        on=['recent_team', 'season', 'week'],
        how='left'
    )
    
    # Fill missing defensive stats with 0
    def_cols = ['def_epa', 'def_yards', 'def_plays', 'def_turnovers', 'def_sacks', 
                'def_epa_per_play', 'def_yards_per_play']
    for col in def_cols:
        if col in team_stats.columns:
            team_stats[col] = team_stats[col].fillna(0)
    
    # Calculate turnover differential
    team_stats['turnover_diff'] = team_stats['def_turnovers'] - team_stats['total_turnovers']
    
    # Success rate: approximate from EPA per play
    # A play is successful if EPA > 0, so success rate ≈ proportion of positive EPA
    # Use a proxy: if EPA per play is positive, estimate higher success rate
    team_stats['success_rate'] = np.clip((team_stats['epa_per_play'] + 0.1) * 10, 0, 1)
    
    # Third down conversion rate: approximate from first downs and plays
    # Proxy: first downs / (plays * 0.3) assuming ~30% of plays are third down
    team_stats['third_down_rate'] = (
        team_stats['total_first_downs'] / (team_stats['total_plays'] * 0.3).replace(0, np.nan)
    ).clip(0, 1)
    
    # Red zone efficiency: approximate from TDs
    # Proxy: TDs / estimated red zone opportunities (rough estimate: 2-3 red zone trips per game)
    team_stats['red_zone_efficiency'] = (team_stats['total_tds'] / 2.5).clip(0, 1)
    
    print(f"    Aggregated stats for {len(team_stats)} team-games")
    
    return team_stats

def compute_rolling_features(team_stats, windows=[3, 5]):
    """
    Compute rolling window features for team stats.
    Uses shift(1) to prevent data leakage (only uses prior games).
    """
    print(f"\nComputing rolling features (windows: {windows})...")
    
    team_features_list = []
    
    for team in team_stats['recent_team'].unique():
        team_data = team_stats[team_stats['recent_team'] == team].copy()
        team_data = team_data.sort_values(['season', 'week']).reset_index(drop=True)
        
        # Rolling windows
        for window in windows:
            # Offensive EPA per play
            team_data[f'epa_per_play_{window}'] = (
                team_data['epa_per_play'].shift(1).rolling(window, min_periods=1).mean()
            )
            
            # Defensive EPA allowed per play
            team_data[f'def_epa_per_play_{window}'] = (
                team_data['def_epa_per_play'].shift(1).rolling(window, min_periods=1).mean()
            )
            
            # Turnover differential
            team_data[f'turnover_diff_{window}'] = (
                team_data['turnover_diff'].shift(1).rolling(window, min_periods=1).mean()
            )
            
            # Sacks allowed (offense)
            team_data[f'sacks_allowed_{window}'] = (
                team_data['sacks_allowed'].shift(1).rolling(window, min_periods=1).mean()
            )
            
            # Sacks made (defense)
            team_data[f'sacks_made_{window}'] = (
                team_data['def_sacks'].shift(1).rolling(window, min_periods=1).mean()
            )
            
            # Yards per play (offense)
            team_data[f'yards_per_play_{window}'] = (
                team_data['yards_per_play'].shift(1).rolling(window, min_periods=1).mean()
            )
            
            # Yards per play (defense)
            team_data[f'def_yards_per_play_{window}'] = (
                team_data['def_yards_per_play'].shift(1).rolling(window, min_periods=1).mean()
            )
            
            # Success rate
            team_data[f'success_rate_{window}'] = (
                team_data['success_rate'].shift(1).rolling(window, min_periods=1).mean()
            )
            
            # Third down conversion rate
            team_data[f'third_down_rate_{window}'] = (
                team_data['third_down_rate'].shift(1).rolling(window, min_periods=1).mean()
            )
            
            # Red zone efficiency
            team_data[f'red_zone_eff_{window}'] = (
                team_data['red_zone_efficiency'].shift(1).rolling(window, min_periods=1).mean()
            )
        
        # Season averages (expanding window)
        team_data['epa_per_play_season'] = (
            team_data['epa_per_play'].shift(1).expanding().mean()
        )
        team_data['def_epa_per_play_season'] = (
            team_data['def_epa_per_play'].shift(1).expanding().mean()
        )
        team_data['turnover_diff_season'] = (
            team_data['turnover_diff'].shift(1).expanding().mean()
        )
        team_data['yards_per_play_season'] = (
            team_data['yards_per_play'].shift(1).expanding().mean()
        )
        team_data['def_yards_per_play_season'] = (
            team_data['def_yards_per_play'].shift(1).expanding().mean()
        )
        team_data['success_rate_season'] = (
            team_data['success_rate'].shift(1).expanding().mean()
        )
        team_data['third_down_rate_season'] = (
            team_data['third_down_rate'].shift(1).expanding().mean()
        )
        team_data['red_zone_eff_season'] = (
            team_data['red_zone_efficiency'].shift(1).expanding().mean()
        )
        
        team_features_list.append(team_data)
    
    team_features = pd.concat(team_features_list, ignore_index=True)
    print(f"    Computed rolling features for {len(team_features)} team-games")
    
    return team_features

def compute_qb_features(raw_weekly):
    """
    Compute QB-specific features.
    Returns QB stats per game with rolling trends.
    """
    print("\nComputing QB-specific features...")
    
    # Filter to QBs only
    qb_data = raw_weekly[
        (raw_weekly['position'] == 'QB') & 
        (raw_weekly['season_type'] == 'REG')
    ].copy()
    
    if len(qb_data) == 0:
        print("    No QB data found")
        return pd.DataFrame()
    
    # Calculate QB metrics
    qb_data['completion_pct'] = qb_data['completions'] / qb_data['attempts'].replace(0, np.nan)
    qb_data['sack_rate'] = qb_data['sacks'] / (qb_data['attempts'] + qb_data['sacks']).replace(0, np.nan)
    qb_data['passing_epa_per_play'] = qb_data['passing_epa'] / (qb_data['attempts'] + qb_data['sacks']).replace(0, np.nan)
    
    # Compute rolling features per QB
    qb_features_list = []
    
    for qb_id in qb_data['player_id'].unique():
        qb_games = qb_data[qb_data['player_id'] == qb_id].copy()
        qb_games = qb_games.sort_values(['season', 'week']).reset_index(drop=True)
        
        # Rolling windows
        for window in [3, 5]:
            qb_games[f'qb_epa_trend_{window}'] = (
                qb_games['passing_epa_per_play'].shift(1).rolling(window, min_periods=1).mean()
            )
            qb_games[f'qb_completion_pct_{window}'] = (
                qb_games['completion_pct'].shift(1).rolling(window, min_periods=1).mean()
            )
            qb_games[f'qb_sack_rate_{window}'] = (
                qb_games['sack_rate'].shift(1).rolling(window, min_periods=1).mean()
            )
        
        qb_features_list.append(qb_games[['recent_team', 'season', 'week', 'player_id', 'player_display_name',
                                          'attempts',
                                          'qb_epa_trend_3', 'qb_epa_trend_5',
                                          'qb_completion_pct_3', 'qb_completion_pct_5',
                                          'qb_sack_rate_3', 'qb_sack_rate_5']])
    
    qb_features = pd.concat(qb_features_list, ignore_index=True)
    
    # Get the starting QB for each team-game (QB with most attempts in that game)
    # If multiple QBs, use the one with most attempts
    qb_starters = qb_features.sort_values(
        ['recent_team', 'season', 'week', 'attempts'],
        ascending=[True, True, True, False]
    ).groupby(['recent_team', 'season', 'week']).first().reset_index()
    # Drop attempts — only needed for sorting, not a downstream feature
    qb_starters = qb_starters.drop(columns=['attempts'], errors='ignore')

    print(f"    Computed QB features for {len(qb_starters)} team-games")
    
    return qb_starters

def merge_features(featurized, team_features, qb_features):
    """
    Merge team and QB features with featurized_games.csv.
    Handles team name matching.
    """
    print("\nMerging features...")
    
    # Check if team names match
    featurized_teams = set(featurized['home_team'].unique()) | set(featurized['away_team'].unique())
    raw_teams = set(team_features['recent_team'].unique())
    
    if featurized_teams != raw_teams:
        print(f"    Warning: Team name mismatch detected")
        print(f"    Featurized teams: {len(featurized_teams)}")
        print(f"    Raw weekly teams: {len(raw_teams)}")
        print(f"    Common teams: {len(featurized_teams & raw_teams)}")
    
    # Merge team features for home team
    featurized = featurized.merge(
        team_features,
        left_on=['season', 'week', 'home_team'],
        right_on=['season', 'week', 'recent_team'],
        how='left',
        suffixes=('', '_home')
    )
    
    # Rename home team features
    feature_cols = [c for c in team_features.columns if c not in ['recent_team', 'season', 'week']]
    rename_dict = {col: f'home_{col}' for col in feature_cols if col in featurized.columns}
    featurized = featurized.rename(columns=rename_dict)
    featurized = featurized.drop(columns=['recent_team'], errors='ignore')
    
    # Merge team features for away team
    featurized = featurized.merge(
        team_features,
        left_on=['season', 'week', 'away_team'],
        right_on=['season', 'week', 'recent_team'],
        how='left',
        suffixes=('', '_away')
    )
    
    # Rename away team features
    rename_dict = {col: f'away_{col}' for col in feature_cols if col in featurized.columns}
    featurized = featurized.rename(columns=rename_dict)
    featurized = featurized.drop(columns=['recent_team'], errors='ignore')
    
    # Merge QB features for home team
    if len(qb_features) > 0:
        # Match by team, season, week
        featurized = featurized.merge(
            qb_features,
            left_on=['season', 'week', 'home_team'],
            right_on=['season', 'week', 'recent_team'],
            how='left',
            suffixes=('', '_home_qb')
        )
        
        # Rename home QB features
        qb_cols = [c for c in qb_features.columns if c not in ['recent_team', 'season', 'week', 'player_id', 'player_display_name']]
        for col in qb_cols:
            if col in featurized.columns:
                featurized = featurized.rename(columns={col: f'home_{col}'})
        
        # Drop merge columns
        featurized = featurized.drop(columns=['recent_team', 'player_id', 'player_display_name'], errors='ignore')
        
        # Merge QB features for away team
        featurized = featurized.merge(
            qb_features,
            left_on=['season', 'week', 'away_team'],
            right_on=['season', 'week', 'recent_team'],
            how='left',
            suffixes=('', '_away_qb')
        )
        
        # Rename away QB features
        for col in qb_cols:
            if col in featurized.columns:
                featurized = featurized.rename(columns={col: f'away_{col}'})
        
        # Drop merge columns
        featurized = featurized.drop(columns=['recent_team', 'player_id', 'player_display_name'], errors='ignore')
    
    print(f"    Merged features: {len(featurized)} games")
    
    return featurized

def handle_missing_values(df):
    """Handle missing values appropriately."""
    print("\nHandling missing values...")
    
    # Get numeric columns
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    
    # Fill missing values with median for numeric columns
    for col in numeric_cols:
        if df[col].isna().sum() > 0:
            median_val = df[col].median()
            if pd.notna(median_val):
                df[col] = df[col].fillna(median_val)
            else:
                df[col] = df[col].fillna(0)
    
    # Count missing values
    missing_counts = df[numeric_cols].isna().sum()
    if missing_counts.sum() > 0:
        print(f"    Warning: {missing_counts.sum()} missing values remaining")
        high_missing = missing_counts[missing_counts > 0].head(10)
        for col, count in high_missing.items():
            print(f"      {col}: {count} ({count/len(df)*100:.1f}%)")
    
    return df

def create_feature_list(df, output_path="data/features_advanced.txt"):
    """Create a text file listing all feature names."""
    print(f"\nCreating feature list: {output_path}")
    
    # Exclude metadata columns
    exclude_cols = [
        'season', 'date', 'week', 'home_team', 'away_team',
        'home_score', 'away_score', 'home_win', 'margin',
        'home_qb_name', 'away_qb_name',
        'home_moneyline', 'away_moneyline', 'spread_line', 'home_spread_odds',
        'away_spread_odds', 'total_line', 'roof', 'surface', 'temp', 'wind', 'stadium'
    ]
    
    feature_cols = [c for c in df.columns if c not in exclude_cols]
    
    with open(output_path, 'w') as f:
        for feat in sorted(feature_cols):
            f.write(f"{feat}\n")
    
    print(f"    Saved {len(feature_cols)} features to {output_path}")
    
    return feature_cols

def main():
    """Main function to prepare advanced dataset."""
    print("="*60)
    print("Advanced Dataset Preparation")
    print("="*60)
    
    # Load data
    raw_weekly, featurized = load_data()
    
    # Aggregate team stats
    team_stats = aggregate_team_stats(raw_weekly)
    
    # Compute rolling features
    team_features = compute_rolling_features(team_stats)
    
    # Compute QB features
    qb_features = compute_qb_features(raw_weekly)
    
    # Merge features
    advanced = merge_features(featurized, team_features, qb_features)
    
    # Handle missing values
    advanced = handle_missing_values(advanced)
    
    # Save output
    output_path = "data/featurized_games_advanced.csv"
    print(f"\nSaving advanced dataset to {output_path}...")
    advanced.to_csv(output_path, index=False)
    print(f"    Saved {len(advanced)} games with {len(advanced.columns)} columns")
    
    # Create feature list
    feature_cols = create_feature_list(advanced)
    
    print("\n" + "="*60)
    print("Summary")
    print("="*60)
    print(f"Total games: {len(advanced)}")
    print(f"Total features: {len(feature_cols)}")
    print(f"Output file: {output_path}")
    print(f"Feature list: data/features_advanced.txt")
    print("\n✅ Advanced dataset preparation complete!")

if __name__ == "__main__":
    main()

