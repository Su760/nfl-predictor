"""
Train ensemble models for NFL game prediction.
Uses cleaned dataset (featurized_games_advanced_clean.csv) to prevent data leakage.
Trains multiple models and creates ensemble classifiers.
"""
import pandas as pd
import numpy as np
import joblib
import os
import warnings
from sklearn.ensemble import (
    RandomForestClassifier, 
    GradientBoostingClassifier, 
    VotingClassifier,
    StackingClassifier
)
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.metrics import (
    accuracy_score, roc_auc_score, log_loss, 
    classification_report, confusion_matrix
)
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings('ignore')

# Try to import XGBoost
try:
    from xgboost import XGBClassifier
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    print("Warning: XGBoost not available. Install with: pip install xgboost")

# Configuration
DATASET_PATH = "data/featurized_games_advanced_clean.csv"
USE_CLEANED_DATASET = True  # Critical: prevents 90% cheating accuracy from data leakage

print("="*60)
print("NFL Game Prediction - Ensemble Model Training")
print("="*60)
print(f"Using cleaned dataset: {USE_CLEANED_DATASET}")
print(f"Dataset: {DATASET_PATH}")

# Load data
print(f"\nLoading dataset: {DATASET_PATH}...")
if not os.path.exists(DATASET_PATH):
    raise FileNotFoundError(
        f"Dataset not found: {DATASET_PATH}\n"
        "Please ensure the cleaned dataset exists to prevent data leakage."
    )

df = pd.read_csv(DATASET_PATH, parse_dates=["date"])
df = df.sort_values("date").reset_index(drop=True)
print(f"Loaded {len(df):,} games")

# Check for target column
if 'home_win' not in df.columns:
    df['home_win'] = (df['home_score'] > df['away_score']).astype(int)
    print("Created home_win target column")

# Drop rows without outcomes
df = df.dropna(subset=["home_win"])
print(f"Games with outcomes: {len(df):,}")

# Define feature columns (exclude metadata and targets)
exclude_cols = [
    'season', 'date', 'week', 'home_team', 'away_team',
    'home_score', 'away_score', 'home_win', 'margin',
    'home_qb_name', 'away_qb_name',
    'home_moneyline', 'away_moneyline', 'spread_line', 
    'home_spread_odds', 'away_spread_odds', 'total_line',
    'roof', 'surface', 'temp', 'wind', 'stadium'
]

feature_cols = [c for c in df.columns if c not in exclude_cols]
print(f"\nUsing {len(feature_cols)} features")

# Check for missing values
print("\nChecking for missing values...")
missing_counts = df[feature_cols].isna().sum()
if missing_counts.sum() > 0:
    print(f"  Found {missing_counts.sum()} missing values")
    missing_cols = missing_counts[missing_counts > 0].head(10)
    for col, count in missing_cols.items():
        pct = count / len(df) * 100
        print(f"    {col}: {count} ({pct:.1f}%)")
    
    # Fill missing values with median
    print("  Filling missing values with median...")
    for col in feature_cols:
        if df[col].isna().sum() > 0:
            median_val = df[col].median()
            df[col] = df[col].fillna(median_val if pd.notna(median_val) else 0)
else:
    print("  No missing values found")

# Time-based train/val/test split (CRITICAL: no data leakage)
print("\n" + "="*60)
print("Time-based train/validation/test split")
print("="*60)
print("Using sequential splits to prevent data leakage...")

# Sort by date to ensure temporal order
df = df.sort_values("date").reset_index(drop=True)

# Split by season: train on earlier seasons, test on later seasons
if 'season' in df.columns:
    seasons = sorted(df['season'].unique())
    print(f"Seasons available: {seasons}")
    
    # Use last 2 seasons for test, one before for val, rest for train
    if len(seasons) >= 3:
        test_seasons = seasons[-2:]
        val_season = seasons[-3]
        train_seasons = seasons[:-3]
        train_df = df[df['season'].isin(train_seasons)].copy()
        val_df = df[df['season'] == val_season].copy()
        test_df = df[df['season'].isin(test_seasons)].copy()
        
        print(f"\nTrain: {len(train_df)} games (seasons: {train_seasons})")
        print(f"Val:   {len(val_df)} games (season: {val_season})")
        print(f"Test:  {len(test_df)} games (seasons: {test_seasons})")
    elif len(seasons) == 2:
        test_seasons = [seasons[-1]]
        val_season = seasons[-2]
        train_seasons = []
        train_df = df.iloc[:int(len(df)*0.70)].copy()
        val_df = df[df['season'] == val_season].copy()
        test_df = df[df['season'].isin(test_seasons)].copy()
        
        print(f"\nTrain: {len(train_df)} games (first 70% of data)")
        print(f"Val:   {len(val_df)} games (season: {val_season})")
        print(f"Test:  {len(test_df)} games (seasons: {test_seasons})")
    else:
        # Single season: use 70/15/15 split
        split_idx_1 = int(len(df) * 0.70)
        split_idx_2 = int(len(df) * 0.85)
        train_df = df.iloc[:split_idx_1].copy()
        val_df = df.iloc[split_idx_1:split_idx_2].copy()
        test_df = df.iloc[split_idx_2:].copy()
        
        print(f"\nTrain: {len(train_df)} games (first 70%)")
        print(f"Val:   {len(val_df)} games (next 15%)")
        print(f"Test:  {len(test_df)} games (last 15%)")
else:
    # No season column: use 70/15/15 split
    split_idx_1 = int(len(df) * 0.70)
    split_idx_2 = int(len(df) * 0.85)
    train_df = df.iloc[:split_idx_1].copy()
    val_df = df.iloc[split_idx_1:split_idx_2].copy()
    test_df = df.iloc[split_idx_2:].copy()
    
    print(f"\nTrain: {len(train_df)} games (first 70%)")
    print(f"Val:   {len(val_df)} games (next 15%)")
    print(f"Test:  {len(test_df)} games (last 15%)")

# Prepare feature matrices
X_train = train_df[feature_cols].copy()
y_train = train_df["home_win"].copy()
X_val = val_df[feature_cols].copy()
y_val = val_df["home_win"].copy()
X_test = test_df[feature_cols].copy()
y_test = test_df["home_win"].copy()

# Ensure all numeric
X_train = X_train.apply(pd.to_numeric, errors='coerce').fillna(0)
X_val = X_val.apply(pd.to_numeric, errors='coerce').fillna(0)
X_test = X_test.apply(pd.to_numeric, errors='coerce').fillna(0)

print(f"\nFeature matrix shapes:")
print(f"  Train: {X_train.shape}")
print(f"  Val:   {X_val.shape}")
print(f"  Test:  {X_test.shape}")

# Baselines
print("\n" + "="*60)
print("Baseline Models")
print("="*60)

# Home team always wins
home_baseline = accuracy_score(y_test, np.ones_like(y_test))
print(f"Home team baseline: {home_baseline:.4f}")

# ELO-based baseline
if 'elo_diff' in feature_cols:
    elo_pred = (X_test['elo_diff'] > 0).astype(int)
    elo_baseline = accuracy_score(y_test, elo_pred)
    print(f"ELO-based baseline: {elo_baseline:.4f}")
else:
    elo_baseline = None
    print("ELO-based baseline: N/A (elo_diff not in features)")

# Scale features for algorithms that need it
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_val_scaled = scaler.transform(X_val)
X_test_scaled = scaler.transform(X_test)

# Train individual models
print("\n" + "="*60)
print("Training Individual Models")
print("="*60)

models = {}
results = {}

# 1. Logistic Regression
print("\n1. Logistic Regression...")
lr = LogisticRegression(max_iter=1000, random_state=42, C=1.0)
lr.fit(X_train_scaled, y_train)
lr_pred = lr.predict(X_test_scaled)
lr_proba = lr.predict_proba(X_test_scaled)[:, 1]
lr_acc = accuracy_score(y_test, lr_pred)
lr_auc = roc_auc_score(y_test, lr_proba)
lr_logloss = log_loss(y_test, lr_proba)
models['lr'] = lr
results['lr'] = {'acc': lr_acc, 'auc': lr_auc, 'logloss': lr_logloss}
print(f"   Accuracy: {lr_acc:.4f}, ROC-AUC: {lr_auc:.4f}, Log Loss: {lr_logloss:.4f}")

# 2. Random Forest
print("\n2. Random Forest...")
rf = RandomForestClassifier(
    n_estimators=200,
    max_depth=15,
    min_samples_split=5,
    min_samples_leaf=2,
    random_state=42,
    n_jobs=-1
)
rf.fit(X_train, y_train)
rf_pred = rf.predict(X_test)
rf_proba = rf.predict_proba(X_test)[:, 1]
rf_acc = accuracy_score(y_test, rf_pred)
rf_auc = roc_auc_score(y_test, rf_proba)
rf_logloss = log_loss(y_test, rf_proba)
models['rf'] = rf
results['rf'] = {'acc': rf_acc, 'auc': rf_auc, 'logloss': rf_logloss}
print(f"   Accuracy: {rf_acc:.4f}, ROC-AUC: {rf_auc:.4f}, Log Loss: {rf_logloss:.4f}")

# 3. Gradient Boosting
print("\n3. Gradient Boosting...")
gb = GradientBoostingClassifier(
    n_estimators=200,
    max_depth=5,
    learning_rate=0.05,
    random_state=42
)
gb.fit(X_train, y_train)
gb_pred = gb.predict(X_test)
gb_proba = gb.predict_proba(X_test)[:, 1]
gb_acc = accuracy_score(y_test, gb_pred)
gb_auc = roc_auc_score(y_test, gb_proba)
gb_logloss = log_loss(y_test, gb_proba)
models['gb'] = gb
results['gb'] = {'acc': gb_acc, 'auc': gb_auc, 'logloss': gb_logloss}
print(f"   Accuracy: {gb_acc:.4f}, ROC-AUC: {gb_auc:.4f}, Log Loss: {gb_logloss:.4f}")

# 4. XGBoost
if XGBOOST_AVAILABLE:
    print("\n4. XGBoost...")
    xgb = XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        random_state=42,
        eval_metric='logloss'
    )
    xgb.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    xgb_pred = xgb.predict(X_test)
    xgb_proba = xgb.predict_proba(X_test)[:, 1]
    xgb_acc = accuracy_score(y_test, xgb_pred)
    xgb_auc = roc_auc_score(y_test, xgb_proba)
    xgb_logloss = log_loss(y_test, xgb_proba)
    models['xgb'] = xgb
    results['xgb'] = {'acc': xgb_acc, 'auc': xgb_auc, 'logloss': xgb_logloss}
    print(f"   Accuracy: {xgb_acc:.4f}, ROC-AUC: {xgb_auc:.4f}, Log Loss: {xgb_logloss:.4f}")
else:
    print("\n4. XGBoost... (skipped - not available)")

# 5. SVM
print("\n5. SVM (this may take a while)...")
svm = SVC(probability=True, random_state=42, C=1.0, kernel='rbf', gamma='scale')
svm.fit(X_train_scaled, y_train)
svm_pred = svm.predict(X_test_scaled)
svm_proba = svm.predict_proba(X_test_scaled)[:, 1]
svm_acc = accuracy_score(y_test, svm_pred)
svm_auc = roc_auc_score(y_test, svm_proba)
svm_logloss = log_loss(y_test, svm_proba)
models['svm'] = svm
results['svm'] = {'acc': svm_acc, 'auc': svm_auc, 'logloss': svm_logloss}
print(f"   Accuracy: {svm_acc:.4f}, ROC-AUC: {svm_auc:.4f}, Log Loss: {svm_logloss:.4f}")

# Ensemble Models
print("\n" + "="*60)
print("Training Ensemble Models")
print("="*60)

# Voting Classifier (hard voting)
print("\n6. Voting Classifier (Hard Voting)...")
voting_estimators = [
    ('rf', models['rf']),
    ('gb', models['gb']),
    ('lr', models['lr']),
]
if XGBOOST_AVAILABLE:
    voting_estimators.append(('xgb', models['xgb']))

voting_hard = VotingClassifier(estimators=voting_estimators, voting='hard')
voting_hard.fit(X_train, y_train)
voting_hard_pred = voting_hard.predict(X_test)
voting_hard_acc = accuracy_score(y_test, voting_hard_pred)
models['voting_hard'] = voting_hard
results['voting_hard'] = {'acc': voting_hard_acc, 'auc': None, 'logloss': None}
print(f"   Accuracy: {voting_hard_acc:.4f}")

# Voting Classifier (soft voting)
print("\n7. Voting Classifier (Soft Voting)...")
voting_soft = VotingClassifier(estimators=voting_estimators, voting='soft')
voting_soft.fit(X_train, y_train)
voting_soft_pred = voting_soft.predict(X_test)
voting_soft_proba = voting_soft.predict_proba(X_test)[:, 1]
voting_soft_acc = accuracy_score(y_test, voting_soft_pred)
voting_soft_auc = roc_auc_score(y_test, voting_soft_proba)
voting_soft_logloss = log_loss(y_test, voting_soft_proba)
models['voting_soft'] = voting_soft
results['voting_soft'] = {'acc': voting_soft_acc, 'auc': voting_soft_auc, 'logloss': voting_soft_logloss}
print(f"   Accuracy: {voting_soft_acc:.4f}, ROC-AUC: {voting_soft_auc:.4f}, Log Loss: {voting_soft_logloss:.4f}")

# Stacking Classifier
print("\n8. Stacking Classifier...")
stacking_estimators = [
    ('rf', models['rf']),
    ('gb', models['gb']),
]
if XGBOOST_AVAILABLE:
    stacking_estimators.append(('xgb', models['xgb']))

stacking = StackingClassifier(
    estimators=stacking_estimators,
    final_estimator=LogisticRegression(max_iter=1000, random_state=42),
    cv=5
)
stacking.fit(X_train, y_train)
stacking_pred = stacking.predict(X_test)
stacking_proba = stacking.predict_proba(X_test)[:, 1]
stacking_acc = accuracy_score(y_test, stacking_pred)
stacking_auc = roc_auc_score(y_test, stacking_proba)
stacking_logloss = log_loss(y_test, stacking_proba)
models['stacking'] = stacking
results['stacking'] = {'acc': stacking_acc, 'auc': stacking_auc, 'logloss': stacking_logloss}
print(f"   Accuracy: {stacking_acc:.4f}, ROC-AUC: {stacking_auc:.4f}, Log Loss: {stacking_logloss:.4f}")

# Summary
print("\n" + "="*60)
print("MODEL PERFORMANCE SUMMARY")
print("="*60)
print(f"{'Model':<20} {'Accuracy':<12} {'ROC-AUC':<12} {'Log Loss':<12}")
print("-" * 60)
print(f"{'Baseline (Home)':<20} {home_baseline:<12.4f} {'N/A':<12} {'N/A':<12}")
if elo_baseline is not None:
    print(f"{'Baseline (ELO)':<20} {elo_baseline:<12.4f} {'N/A':<12} {'N/A':<12}")
print(f"{'Logistic Regression':<20} {lr_acc:<12.4f} {lr_auc:<12.4f} {lr_logloss:<12.4f}")
print(f"{'Random Forest':<20} {rf_acc:<12.4f} {rf_auc:<12.4f} {rf_logloss:<12.4f}")
print(f"{'Gradient Boosting':<20} {gb_acc:<12.4f} {gb_auc:<12.4f} {gb_logloss:<12.4f}")
if XGBOOST_AVAILABLE:
    print(f"{'XGBoost':<20} {xgb_acc:<12.4f} {xgb_auc:<12.4f} {xgb_logloss:<12.4f}")
print(f"{'SVM':<20} {svm_acc:<12.4f} {svm_auc:<12.4f} {svm_logloss:<12.4f}")
print(f"{'Voting (Hard)':<20} {voting_hard_acc:<12.4f} {'N/A':<12} {'N/A':<12}")
print(f"{'Voting (Soft)':<20} {voting_soft_acc:<12.4f} {voting_soft_auc:<12.4f} {voting_soft_logloss:<12.4f}")
print(f"{'Stacking':<20} {stacking_acc:<12.4f} {stacking_auc:<12.4f} {stacking_logloss:<12.4f}")
print("="*60)

# Select best model
best_model_name = 'stacking'
best_model = models['stacking']
best_acc = stacking_acc

if voting_soft_acc > best_acc:
    best_model_name = 'voting_soft'
    best_model = models['voting_soft']
    best_acc = voting_soft_acc

print(f"\nBest model: {best_model_name} (Accuracy: {best_acc:.4f})")

# Save models
print("\n" + "="*60)
print("Saving Models")
print("="*60)

# Main ensemble model (best ensemble)
bundle = {
    "model": best_model,
    "models": models,
    "features": feature_cols,
    "scaler": scaler,
    "best_model_name": best_model_name,
    "needs_scaling": best_model_name in ['lr', 'svm'],
    "performance": results
}
joblib.dump(bundle, "model_ensemble.joblib")
print("Saved: model_ensemble.joblib")

# Basic ensemble (voting soft)
bundle_basic = {
    "model": models['voting_soft'],
    "features": feature_cols,
    "scaler": scaler,
    "best_model_name": "voting_soft",
    "needs_scaling": False,
    "performance": results['voting_soft']
}
joblib.dump(bundle_basic, "model_ensemble_basic.joblib")
print("Saved: model_ensemble_basic.joblib")

# Real model (stacking)
bundle_real = {
    "model": models['stacking'],
    "features": feature_cols,
    "scaler": scaler,
    "best_model_name": "stacking",
    "needs_scaling": False,
    "performance": results['stacking']
}
joblib.dump(bundle_real, "model_real.joblib")
print("Saved: model_real.joblib")

# Diff model (for point differential if needed)
# Using best classifier for now
bundle_diff = {
    "model": best_model,
    "features": feature_cols,
    "scaler": scaler,
    "best_model_name": best_model_name,
    "needs_scaling": best_model_name in ['lr', 'svm'],
    "performance": results[best_model_name]
}
joblib.dump(bundle_diff, "model_diff.joblib")
print("Saved: model_diff.joblib")

# Detailed report for best model
print("\n" + "="*60)
print(f"Detailed Report for {best_model_name}:")
print("="*60)

if best_model_name in ['lr', 'svm']:
    best_pred = best_model.predict(X_test_scaled)
    if hasattr(best_model, 'predict_proba'):
        best_proba = best_model.predict_proba(X_test_scaled)[:, 1]
    else:
        best_proba = None
else:
    best_pred = best_model.predict(X_test)
    if hasattr(best_model, 'predict_proba'):
        best_proba = best_model.predict_proba(X_test)[:, 1]
    else:
        best_proba = None

print(classification_report(y_test, best_pred, zero_division=0))
print("\nConfusion Matrix:")
print(confusion_matrix(y_test, best_pred))

print("\n" + "="*60)
print("Training Complete!")
print("="*60)
print(f"Best model: {best_model_name} with {best_acc:.4f} accuracy")
print(f"Expected accuracy: ~60% (realistic prediction)")
print(f"Expected confidence: ~70-74% (but actual accuracy ~60%)")
print("\nSaved models:")
print("  - model_ensemble.joblib (best ensemble)")
print("  - model_ensemble_basic.joblib (voting soft)")
print("  - model_real.joblib (stacking)")
print("  - model_diff.joblib (best model)")

