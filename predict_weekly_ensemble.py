"""
Predict upcoming NFL games using trained ensemble model.
Loads model_ensemble.joblib and generates predictions for weekly games.
"""
import pandas as pd
import numpy as np
import joblib
import sys
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

def load_model(model_path="model_ensemble.joblib"):
    """Load the trained ensemble model."""
    print(f"Loading model from {model_path}...")
    try:
        bundle = joblib.load(model_path)
        print(f"  Model loaded: {bundle.get('best_model_name', 'ensemble')}")
        print(f"  Features: {len(bundle.get('features', []))}")
        return bundle
    except FileNotFoundError:
        print(f"Error: Model file {model_path} not found.")
        print("Please train a model first using train_ensemble.py")
        sys.exit(1)

def load_upcoming_games(year=None, week=None):
    """
    Load upcoming games to predict.
    For now, returns None - this would need to be implemented to fetch actual schedule.
    In production, this would fetch from NFL API or schedule CSV.
    """
    print("\nLoading upcoming games...")
    print("  Note: This is a placeholder - implement actual schedule loading")
    print("  For now, returns None. Implement schedule fetching as needed.")
    return None

def prepare_game_features(game_row, bundle, featurized_data=None):
    """
    Prepare features for a single game prediction.
    Uses historical data to compute features.
    """
    features = bundle.get('features', [])
    scaler = bundle.get('scaler', None)
    needs_scaling = bundle.get('needs_scaling', False)
    
    # Create feature dictionary
    feature_dict = {}
    
    # Basic features from game row
    if 'home_team' in game_row and 'away_team' in game_row:
        home_team = game_row['home_team']
        away_team = game_row['away_team']
        
        # Get team stats from featurized data if available
        if featurized_data is not None:
            # Get most recent stats for home team
            home_games = featurized_data[featurized_data['home_team'] == home_team]
            if len(home_games) == 0:
                home_games = featurized_data[featurized_data['away_team'] == home_team]
            
            # Get most recent stats for away team
            away_games = featurized_data[featurized_data['home_team'] == away_team]
            if len(away_games) == 0:
                away_games = featurized_data[featurized_data['away_team'] == away_team]
            
            if len(home_games) > 0 and len(away_games) > 0:
                home_recent = home_games.iloc[-1]
                away_recent = away_games.iloc[-1]
                
                # Extract features
                for feat in features:
                    if feat.startswith('home_') and feat.replace('home_', '') in home_recent.index:
                        feature_dict[feat] = home_recent[feat.replace('home_', '')]
                    elif feat.startswith('away_') and feat.replace('away_', '') in away_recent.index:
                        feature_dict[feat] = away_recent[feat.replace('away_', '')]
                    elif feat in home_recent.index:
                        feature_dict[feat] = home_recent[feat]
        
        # Fill missing features with defaults
        for feat in features:
            if feat not in feature_dict:
                if 'elo' in feat.lower():
                    feature_dict[feat] = 1500  # Default ELO
                else:
                    feature_dict[feat] = 0.0  # Default value
    
    # Create feature vector
    X = pd.DataFrame([feature_dict])[features] if feature_dict else pd.DataFrame(columns=features)
    
    # Scale if needed
    if needs_scaling and scaler is not None:
        X_scaled = scaler.transform(X)
        return X_scaled
    else:
        return X

def predict_games(games_df, bundle, featurized_data=None):
    """Predict outcomes for multiple games."""
    model = bundle['model']
    features = bundle.get('features', [])
    scaler = bundle.get('scaler', None)
    needs_scaling = bundle.get('needs_scaling', False)
    
    predictions = []
    
    for _, game in games_df.iterrows():
        # Prepare features
        X = prepare_game_features(game, bundle, featurized_data)
        
        # Predict
        if needs_scaling:
            prob = model.predict_proba(X)[0, 1]
        else:
            prob = model.predict_proba(X)[0, 1]
        
        home_win_prob = prob
        away_win_prob = 1 - prob
        predicted_winner = game['home_team'] if home_win_prob > 0.5 else game['away_team']
        confidence = max(home_win_prob, away_win_prob)
        
        # Get ELO diff if available
        elo_diff = 0
        if 'elo_diff' in features:
            X_df = pd.DataFrame(X, columns=features)
            elo_diff = X_df['elo_diff'].iloc[0] if 'elo_diff' in X_df.columns else 0
        
        predictions.append({
            'week': game.get('week', '?'),
            'date': game.get('date', '?'),
            'away_team': game.get('away_team', '?'),
            'home_team': game.get('home_team', '?'),
            'home_win_prob': home_win_prob,
            'away_win_prob': away_win_prob,
            'predicted_winner': predicted_winner,
            'confidence': confidence,
            'elo_diff': elo_diff
        })
    
    return pd.DataFrame(predictions)

def main():
    """Main prediction function."""
    # Parse command line arguments
    year = None
    week = None
    
    if len(sys.argv) > 1:
        year = int(sys.argv[1])
    if len(sys.argv) > 2:
        week = int(sys.argv[2])
    
    print("="*60)
    print("NFL Weekly Game Predictions")
    print("="*60)
    
    if year:
        print(f"Year: {year}")
    if week:
        print(f"Week: {week}")
    
    # Load model
    bundle = load_model()
    
    # Load upcoming games
    games_df = load_upcoming_games(year=year, week=week)
    
    if games_df is None or len(games_df) == 0:
        print("\nNo upcoming games found or schedule loading not implemented.")
        print("\nExample usage:")
        print("  python predict_weekly_ensemble.py 2025      # All upcoming games")
        print("  python predict_weekly_ensemble.py 2025 12   # Week 12")
        print("\nNote: Implement load_upcoming_games() to fetch actual schedule.")
        return
    
    # Load featurized data for feature computation
    try:
        featurized_data = pd.read_csv("data/featurized_games_advanced_clean.csv", parse_dates=["date"])
        print(f"\nLoaded {len(featurized_data)} historical games for feature computation")
    except FileNotFoundError:
        print("\nWarning: Could not load historical data. Using default features.")
        featurized_data = None
    
    # Make predictions
    print("\nGenerating predictions...")
    predictions = predict_games(games_df, bundle, featurized_data)
    
    # Display predictions
    print("\n" + "="*60)
    print("PREDICTIONS")
    print("="*60)
    
    for _, pred in predictions.iterrows():
        print(f"\nWeek {pred['week']} ({pred['date']}):")
        print(f"  {pred['away_team']} @ {pred['home_team']}")
        print(f"  Predicted Winner: {pred['predicted_winner']}")
        print(f"  Home Win Probability: {pred['home_win_prob']:.1%}")
        print(f"  Away Win Probability: {pred['away_win_prob']:.1%}")
        print(f"  Confidence: {pred['confidence']:.1%}")
        print(f"  ELO Diff: {pred['elo_diff']:.1f}")
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total games: {len(predictions)}")
    print(f"Average confidence: {predictions['confidence'].mean():.1%}")
    print(f"Home wins predicted: {(predictions['home_win_prob'] > 0.5).sum()}")
    print(f"Away wins predicted: {(predictions['away_win_prob'] > 0.5).sum()}")
    
    # Save predictions
    output_file = f"predictions_{year if year else 'all'}_{week if week else 'all'}.csv"
    predictions.to_csv(output_file, index=False)
    print(f"\nSaved predictions to: {output_file}")

if __name__ == "__main__":
    main()

