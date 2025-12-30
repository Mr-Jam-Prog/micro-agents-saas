"""
Integration tests for A/B Testing framework and algorithms.
Tests statistical methods, randomization, bias detection, and result interpretation.
"""

import asyncio
import math
import random
import statistics
import time
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from scipy.stats import chi2_contingency, ttest_ind, norm

from microagents.experiments.ab_testing import (
    ABTest, 
    Experiment,
    Variant,
    StatisticalTest,
    SampleSizeCalculator,
    RandomizationEngine,
    BiasDetector,
    ResultInterpreter,
    BanditAlgorithm,
    MultiVariateTest,
    PersonalizationEngine,
    ROIComparator
)
from microagents.experiments.metrics import (
    ConversionMetric,
    RevenueMetric,
    PerformanceMetric,
    SecurityMetric,
    ComplianceMetric
)
from microagents.core.logging import get_logger

logger = get_logger(__name__)


class TestABTesting:
    """Integration tests for A/B testing framework."""
    
    # Sample test configurations
    FEATURE_FLAG_CONFIG = {
        "experiment_name": "new_checkout_flow",
        "description": "Test new checkout flow vs old",
        "hypothesis": "New checkout flow increases conversion rate by 10%",
        "primary_metric": "conversion_rate",
        "significance_level": 0.05,
        "power": 0.80,
        "min_detectable_effect": 0.10,
        "variants": [
            {
                "name": "control",
                "weight": 0.5,
                "description": "Current checkout flow",
                "configuration": {"feature_enabled": False}
            },
            {
                "name": "treatment",
                "weight": 0.5,
                "description": "New checkout flow",
                "configuration": {"feature_enabled": True}
            }
        ],
        "target_population": "all_users",
        "exclusion_criteria": ["beta_testers", "internal_users"],
        "estimated_traffic": 10000,
        "duration_days": 14,
        "success_criteria": {
            "conversion_rate": {"min_improvement": 0.05, "statistical_significance": 0.05}
        }
    }
    
    ALGORITHM_COMPARISON_CONFIG = {
        "experiment_name": "recommendation_algorithm_v3",
        "description": "Compare new ML algorithm vs current",
        "hypothesis": "New algorithm improves click-through rate by 15%",
        "primary_metric": "click_through_rate",
        "secondary_metrics": ["engagement_time", "purchase_rate"],
        "significance_level": 0.01,  # Stricter for algorithm changes
        "power": 0.90,
        "min_detectable_effect": 0.15,
        "variants": [
            {
                "name": "control",
                "weight": 0.33,
                "description": "Current collaborative filtering",
                "algorithm": "collaborative_filtering_v2"
            },
            {
                "name": "treatment_a",
                "weight": 0.33,
                "description": "Neural matrix factorization",
                "algorithm": "neural_mf_v1"
            },
            {
                "name": "treatment_b",
                "weight": 0.34,
                "description": "Transformer-based recommendations",
                "algorithm": "transformer_rec_v1"
            }
        ],
        "target_population": "active_users",
        "segmentation": ["user_tier", "geographic_region"],
        "adaptive_allocation": True,
        "multi_armed_bandit": "thompson_sampling"
    }
    
    PRICING_TEST_CONFIG = {
        "experiment_name": "premium_tier_pricing",
        "description": "Test different price points for premium tier",
        "hypothesis": "$19.99 price point maximizes revenue",
        "primary_metric": "revenue_per_user",
        "secondary_metrics": ["conversion_rate", "churn_rate"],
        "significance_level": 0.05,
        "power": 0.80,
        "min_detectable_effect": 0.10,
        "variants": [
            {
                "name": "control",
                "weight": 0.25,
                "price": 14.99,
                "description": "Current price point"
            },
            {
                "name": "treatment_a",
                "weight": 0.25,
                "price": 17.99,
                "description": "Moderate increase"
            },
            {
                "name": "treatment_b",
                "weight": 0.25,
                "price": 19.99,
                "description": "Target price point"
            },
            {
                "name": "treatment_c",
                "weight": 0.25,
                "price": 24.99,
                "description": "Premium price point"
            }
        ],
        "target_population": "free_tier_users",
        "segmentation": ["user_engagement", "company_size"],
        "guardrail_metrics": ["churn_rate"],
        "success_criteria": {
            "revenue_per_user": {"min_improvement": 0.10},
            "churn_rate": {"max_increase": 0.02}
        }
    }
    
    PERFORMANCE_TEST_CONFIG = {
        "experiment_name": "caching_strategy_performance",
        "description": "Test different caching strategies for API performance",
        "hypothesis": "Redis cluster caching reduces p95 latency by 30%",
        "primary_metric": "p95_latency_ms",
        "secondary_metrics": ["throughput_rps", "error_rate"],
        "significance_level": 0.05,
        "power": 0.90,
        "min_detectable_effect": 0.30,
        "variants": [
            {
                "name": "control",
                "weight": 0.5,
                "caching_strategy": "local_memory",
                "ttl_seconds": 300
            },
            {
                "name": "treatment",
                "weight": 0.5,
                "caching_strategy": "redis_cluster",
                "ttl_seconds": 600
            }
        ],
        "target_population": "api_endpoints",
        "sample_size_per_variant": 100000,  # Large sample for performance metrics
        "warmup_period_minutes": 30,
        "success_criteria": {
            "p95_latency_ms": {"max_value": 100, "improvement_percentage": 30},
            "error_rate": {"max_value": 0.001}
        }
    }
    
    @pytest.fixture
    def feature_flag_experiment(self):
        """Fixture providing feature flag A/B test."""
        return Experiment.from_config(self.FEATURE_FLAG_CONFIG)
    
    @pytest.fixture
    def algorithm_experiment(self):
        """Fixture providing algorithm comparison A/B test."""
        return Experiment.from_config(self.ALGORITHM_COMPARISON_CONFIG)
    
    @pytest.fixture
    def pricing_experiment(self):
        """Fixture providing pricing A/B test."""
        return Experiment.from_config(self.PRICING_TEST_CONFIG)
    
    @pytest.fixture
    def performance_experiment(self):
        """Fixture providing performance A/B test."""
        return Experiment.from_config(self.PERFORMANCE_TEST_CONFIG)
    
    @pytest.fixture
    def sample_size_calculator(self):
        """Fixture providing sample size calculator."""
        return SampleSizeCalculator()
    
    @pytest.fixture
    def statistical_test(self):
        """Fixture providing statistical test engine."""
        return StatisticalTest()
    
    @pytest.fixture
    def randomization_engine(self):
        """Fixture providing randomization engine."""
        return RandomizationEngine(seed=42)
    
    @pytest.fixture
    def bias_detector(self):
        """Fixture providing bias detector."""
        return BiasDetector()
    
    @pytest.fixture
    def result_interpreter(self):
        """Fixture providing result interpreter."""
        return ResultInterpreter()
    
    @pytest.fixture
    def bandit_algorithm(self):
        """Fixture providing bandit algorithm."""
        return BanditAlgorithm(strategy="thompson_sampling")
    
    @pytest.fixture
    def multivariate_test(self):
        """Fixture providing multivariate test engine."""
        return MultiVariateTest()
    
    @pytest.fixture
    def personalization_engine(self):
        """Fixture providing personalization engine."""
        return PersonalizationEngine()
    
    @pytest.fixture
    def roi_comparator(self):
        """Fixture providing ROI comparator."""
        return ROIComparator()
    
    @pytest.fixture
    def simulated_conversion_data(self):
        """Fixture providing simulated conversion data."""
        np.random.seed(42)
        
        # Control group: 10% conversion rate
        control_conversions = np.random.binomial(1, 0.10, size=5000)
        control_revenue = np.where(
            control_conversions == 1,
            np.random.normal(50, 10, size=5000),
            0
        )
        
        # Treatment group: 12% conversion rate (20% improvement)
        treatment_conversions = np.random.binomial(1, 0.12, size=5000)
        treatment_revenue = np.where(
            treatment_conversions == 1,
            np.random.normal(52, 10, size=5000),  # Slightly higher average revenue
            0
        )
        
        return {
            "control": {
                "user_ids": [f"control_{i}" for i in range(5000)],
                "conversions": control_conversions.tolist(),
                "revenue": control_revenue.tolist(),
                "conversion_rate": control_conversions.mean(),
                "avg_revenue": control_revenue[control_conversions == 1].mean()
            },
            "treatment": {
                "user_ids": [f"treatment_{i}" for i in range(5000)],
                "conversions": treatment_conversions.tolist(),
                "revenue": treatment_revenue.tolist(),
                "conversion_rate": treatment_conversions.mean(),
                "avg_revenue": treatment_revenue[treatment_conversions == 1].mean()
            }
        }
    
    @pytest.fixture
    def simulated_performance_data(self):
        """Fixture providing simulated performance data."""
        np.random.seed(42)
        
        # Control group: higher latency
        control_latency = np.random.exponential(scale=50, size=10000)
        control_latency = np.clip(control_latency, 10, 200)
        
        # Treatment group: lower latency
        treatment_latency = np.random.exponential(scale=35, size=10000)
        treatment_latency = np.clip(treatment_latency, 10, 150)
        
        return {
            "control": {
                "latency_ms": control_latency.tolist(),
                "throughput_rps": np.random.normal(1000, 100, 10000).tolist(),
                "error_rate": np.random.beta(2, 998, 10000).tolist()  # ~0.2% error rate
            },
            "treatment": {
                "latency_ms": treatment_latency.tolist(),
                "throughput_rps": np.random.normal(1050, 100, 10000).tolist(),
                "error_rate": np.random.beta(1, 999, 10000).tolist()  # ~0.1% error rate
            }
        }
    
    # Test Group 1: Statistical Significance Validation
    class TestStatisticalSignificance:
        """Tests for statistical significance validation."""
        
        def test_z_test_conversion_rates(self, statistical_test, simulated_conversion_data):
            """Test Z-test for conversion rate comparison."""
            control_data = simulated_conversion_data["control"]
            treatment_data = simulated_conversion_data["treatment"]
            
            # Calculate conversion rates
            control_rate = control_data["conversion_rate"]
            treatment_rate = treatment_data["conversion_rate"]
            
            # Perform Z-test
            result = statistical_test.z_test_proportions(
                n1=len(control_data["conversions"]),
                successes1=sum(control_data["conversions"]),
                n2=len(treatment_data["conversions"]),
                successes2=sum(treatment_data["conversions"]),
                alternative="greater"  # Treatment > Control
            )
            
            # Verify results
            assert "z_statistic" in result
            assert "p_value" in result
            assert "significant" in result
            assert "effect_size" in result
            
            # With 20% improvement, should be significant
            assert result["significant"] is True
            assert result["p_value"] < 0.05
            
            # Calculate relative improvement
            relative_improvement = (treatment_rate - control_rate) / control_rate
            assert relative_improvement > 0.15  # Should be ~20% improvement
            
            print(f"\nZ-test for conversion rates:")
            print(f"  Control rate: {control_rate:.3%}")
            print(f"  Treatment rate: {treatment_rate:.3%}")
            print(f"  Relative improvement: {relative_improvement:.2%}")
            print(f"  Z-statistic: {result['z_statistic']:.3f}")
            print(f"  P-value: {result['p_value']:.6f}")
            print(f"  Significant: {result['significant']}")
            
        def test_t_test_continuous_metrics(self, statistical_test, simulated_performance_data):
            """Test T-test for continuous metrics (performance)."""
            control_data = simulated_performance_data["control"]
            treatment_data = simulated_performance_data["treatment"]
            
            # Perform T-test for latency
            result = statistical_test.t_test_independent(
                sample1=control_data["latency_ms"],
                sample2=treatment_data["latency_ms"],
                alternative="less"  # Treatment < Control (lower latency is better)
            )
            
            # Verify results
            assert "t_statistic" in result
            assert "p_value" in result
            assert "significant" in result
            assert "mean_diff" in result
            assert "ci_lower" in result
            assert "ci_upper" in result
            
            # Should be significant (treatment has lower latency)
            assert result["significant"] is True
            assert result["p_value"] < 0.05
            assert result["mean_diff"] < 0  # Negative means treatment is better
            
            # Calculate percentage improvement
            control_mean = np.mean(control_data["latency_ms"])
            treatment_mean = np.mean(treatment_data["latency_ms"])
            percent_improvement = (control_mean - treatment_mean) / control_mean * 100
            
            print(f"\nT-test for latency:")
            print(f"  Control mean: {control_mean:.2f} ms")
            print(f"  Treatment mean: {treatment_mean:.2f} ms")
            print(f"  Improvement: {percent_improvement:.1f}%")
            print(f"  T-statistic: {result['t_statistic']:.3f}")
            print(f"  P-value: {result['p_value']:.6f}")
            print(f"  95% CI: [{result['ci_lower']:.3f}, {result['ci_upper']:.3f}]")
            
        def test_chi_square_test_categorical(self, statistical_test):
            """Test Chi-square test for categorical data."""
            # Simulate categorical data (e.g., user preferences)
            np.random.seed(42)
            
            # Control: preference distribution
            control_counts = [200, 150, 100, 50]  # 4 categories
            
            # Treatment: shifted preferences
            treatment_counts = [180, 170, 120, 80]  # More in categories 3 and 4
            
            # Perform Chi-square test
            result = statistical_test.chi_square_test(
                observed=[control_counts, treatment_counts]
            )
            
            # Verify results
            assert "chi2_statistic" in result
            assert "p_value" in result
            assert "significant" in result
            assert "degrees_of_freedom" in result
            
            # May or may not be significant depending on effect size
            print(f"\nChi-square test for categorical data:")
            print(f"  Control distribution: {control_counts}")
            print(f"  Treatment distribution: {treatment_counts}")
            print(f"  Chi2 statistic: {result['chi2_statistic']:.3f}")
            print(f"  P-value: {result['p_value']:.6f}")
            print(f"  Significant: {result['significant']}")
            
        def test_multiple_comparison_correction(self, statistical_test):
            """Test correction for multiple comparisons."""
            # Simulate testing multiple metrics
            metrics = ["conversion_rate", "click_through_rate", "revenue_per_user", "engagement_time"]
            
            # Simulated p-values (some significant by chance)
            p_values = [0.01, 0.03, 0.25, 0.04]
            
            # Apply Bonferroni correction
            corrected = statistical_test.bonferroni_correction(p_values, alpha=0.05)
            
            # Verify correction
            assert len(corrected["corrected_p_values"]) == len(p_values)
            assert len(corrected["significant"]) == len(p_values)
            
            # After correction, fewer should be significant
            original_significant = sum(1 for p in p_values if p < 0.05)
            corrected_significant = sum(corrected["significant"])
            
            assert corrected_significant <= original_significant
            
            print(f"\nMultiple comparison correction:")
            print(f"  Original p-values: {p_values}")
            print(f"  Corrected p-values: {corrected['corrected_p_values']}")
            print(f"  Original significant: {original_significant}")
            print(f"  Corrected significant: {corrected_significant}")
            
        def test_sequential_testing(self, statistical_test):
            """Test sequential testing (peeking)."""
            np.random.seed(42)
            
            # Simulate sequential data collection
            control_conversions = []
            treatment_conversions = []
            
            sequential_results = []
            
            for i in range(1, 11):  # 10 peeks
                # Add batch of 100 users to each group
                control_batch = np.random.binomial(1, 0.10, size=100)
                treatment_batch = np.random.binomial(1, 0.12, size=100)
                
                control_conversions.extend(control_batch)
                treatment_conversions.extend(treatment_batch)
                
                # Test at each peek
                result = statistical_test.z_test_proportions(
                    n1=len(control_conversions),
                    successes1=sum(control_conversions),
                    n2=len(treatment_conversions),
                    successes2=sum(treatment_conversions),
                    alpha=0.05,
                    sequential=True,
                    peek_number=i
                )
                
                sequential_results.append({
                    "sample_size": len(control_conversions),
                    "p_value": result["p_value"],
                    "significant": result["significant"]
                })
            
            # Should eventually become significant
            final_significant = any(r["significant"] for r in sequential_results)
            
            print(f"\nSequential testing (peeking):")
            for i, result in enumerate(sequential_results, 1):
                print(f"  Peek {i}: n={result['sample_size']}, p={result['p_value']:.4f}, "
                      f"significant={result['significant']}")
            
            assert final_significant is True
            
    # Test Group 2: Sample Size Calculation
    class TestSampleSizeCalculation:
        """Tests for sample size calculation."""
        
        def test_sample_size_binary_metric(self, sample_size_calculator):
            """Test sample size calculation for binary metric (conversion rate)."""
            # Parameters
            baseline_rate = 0.10  # 10% conversion rate
            min_detectable_effect = 0.10  # 10% relative improvement
            significance_level = 0.05
            power = 0.80
            allocation_ratio = 1.0  # Equal allocation
            
            # Calculate sample size
            result = sample_size_calculator.for_binary_metric(
                baseline_rate=baseline_rate,
                min_detectable_effect=min_detectable_effect,
                alpha=significance_level,
                power=power,
                allocation_ratio=allocation_ratio
            )
            
            # Verify calculation
            assert "sample_size_per_variant" in result
            assert "total_sample_size" in result
            assert "effect_size" in result
            assert "detectable_rate" in result
            
            detectable_rate = baseline_rate * (1 + min_detectable_effect)
            assert result["detectable_rate"] == pytest.approx(detectable_rate, rel=1e-3)
            
            print(f"\nSample size for binary metric:")
            print(f"  Baseline rate: {baseline_rate:.2%}")
            print(f"  MDE: {min_detectable_effect:.1%}")
            print(f"  Detectable rate: {result['detectable_rate']:.2%}")
            print(f"  Sample per variant: {result['sample_size_per_variant']:,}")
            print(f"  Total sample: {result['total_sample_size']:,}")
            
        def test_sample_size_continuous_metric(self, sample_size_calculator):
            """Test sample size calculation for continuous metric (revenue)."""
            # Parameters
            baseline_mean = 50.0
            baseline_std = 10.0
            min_detectable_effect = 5.0  # Absolute difference
            significance_level = 0.05
            power = 0.80
            
            # Calculate sample size
            result = sample_size_calculator.for_continuous_metric(
                baseline_mean=baseline_mean,
                baseline_std=baseline_std,
                min_detectable_effect=min_detectable_effect,
                alpha=significance_level,
                power=power
            )
            
            # Verify calculation
            assert "sample_size_per_variant" in result
            assert "effect_size_cohens_d" in result
            
            # Cohen's d should be effect_size / std
            expected_cohens_d = min_detectable_effect / baseline_std
            assert result["effect_size_cohens_d"] == pytest.approx(expected_cohens_d, rel=1e-3)
            
            print(f"\nSample size for continuous metric:")
            print(f"  Baseline mean: {baseline_mean:.2f}")
            print(f"  Baseline std: {baseline_std:.2f}")
            print(f"  MDE: {min_detectable_effect:.2f}")
            print(f"  Cohen's d: {result['effect_size_cohens_d']:.3f}")
            print(f"  Sample per variant: {result['sample_size_per_variant']:,}")
            
        def test_duration_calculation(self, sample_size_calculator):
            """Test experiment duration calculation."""
            # Parameters
            sample_size_per_variant = 5000
            daily_traffic_per_variant = 1000
            ramp_up_percentage = 0.2  # Start with 20% traffic
            
            # Calculate duration
            result = sample_size_calculator.estimate_duration(
                sample_size_per_variant=sample_size_per_variant,
                daily_traffic_per_variant=daily_traffic_per_variant,
                ramp_up_percentage=ramp_up_percentage
            )
            
            # Verify calculation
            assert "days_without_ramp" in result
            assert "days_with_ramp" in result
            assert "ramp_up_days" in result
            assert "total_traffic" in result
            
            # With ramp-up, should take longer
            assert result["days_with_ramp"] > result["days_without_ramp"]
            
            print(f"\nExperiment duration estimation:")
            print(f"  Sample per variant: {sample_size_per_variant:,}")
            print(f"  Daily traffic: {daily_traffic_per_variant:,}")
            print(f"  Ramp-up: {ramp_up_percentage:.0%}")
            print(f"  Days without ramp: {result['days_without_ramp']:.1f}")
            print(f"  Days with ramp: {result['days_with_ramp']:.1f}")
            
        def test_power_analysis(self, sample_size_calculator):
            """Test power analysis for given sample size."""
            # Parameters
            baseline_rate = 0.10
            treatment_rate = 0.12  # 20% improvement
            sample_size_per_variant = 5000
            significance_level = 0.05
            
            # Calculate power
            result = sample_size_calculator.power_analysis(
                baseline_rate=baseline_rate,
                treatment_rate=treatment_rate,
                sample_size_per_variant=sample_size_per_variant,
                alpha=significance_level
            )
            
            # Verify calculation
            assert "power" in result
            assert "detectable_effect" in result
            
            # With large sample size, power should be high
            assert result["power"] > 0.80
            
            print(f"\nPower analysis:")
            print(f"  Baseline rate: {baseline_rate:.2%}")
            print(f"  Treatment rate: {treatment_rate:.2%}")
            print(f"  Sample per variant: {sample_size_per_variant:,}")
            print(f"  Power: {result['power']:.3f}")
            print(f"  Detectable effect: {result['detectable_effect']:.2%}")
            
        def test_multiple_variant_sample_size(self, sample_size_calculator):
            """Test sample size calculation for multiple variants."""
            # Parameters for 3 variants
            baseline_rate = 0.10
            min_detectable_effect = 0.10
            significance_level = 0.05
            power = 0.80
            num_variants = 3
            
            # Calculate for multiple variants
            result = sample_size_calculator.for_multiple_variants(
                baseline_rate=baseline_rate,
                min_detectable_effect=min_detectable_effect,
                alpha=significance_level,
                power=power,
                num_variants=num_variants,
                comparison_type="dunnett"  # Compare each treatment to control
            )
            
            # Verify calculation
            assert "sample_size_per_variant" in result
            assert "total_sample_size" in result
            
            # Should be larger than 2-variant test
            two_variant_result = sample_size_calculator.for_binary_metric(
                baseline_rate=baseline_rate,
                min_detectable_effect=min_detectable_effect,
                alpha=significance_level,
                power=power
            )
            
            assert result["sample_size_per_variant"] > two_variant_result["sample_size_per_variant"]
            
            print(f"\nSample size for multiple variants:")
            print(f"  Number of variants: {num_variants}")
            print(f"  Sample per variant: {result['sample_size_per_variant']:,}")
            print(f"  Total sample: {result['total_sample_size']:,}")
            
    # Test Group 3: Randomization Testing
    class TestRandomization:
        """Tests for randomization and assignment."""
        
        def test_random_assignment_uniform(self, randomization_engine):
            """Test uniform random assignment."""
            user_ids = [f"user_{i}" for i in range(1000)]
            variants = ["control", "treatment"]
            weights = [0.5, 0.5]
            
            # Assign users to variants
            assignments = randomization_engine.assign_users(
                user_ids=user_ids,
                variants=variants,
                weights=weights
            )
            
            # Verify assignments
            assert len(assignments) == len(user_ids)
            
            # Count assignments
            from collections import Counter
            counts = Counter(assignments.values())
            
            # Should be approximately 50/50 split
            control_count = counts.get("control", 0)
            treatment_count = counts.get("treatment", 0)
            
            ratio = control_count / len(user_ids)
            assert 0.45 <= ratio <= 0.55  # Within 5% of 50/50
            
            print(f"\nRandom assignment (uniform):")
            print(f"  Total users: {len(user_ids)}")
            print(f"  Control: {control_count} ({control_count/len(user_ids):.1%})")
            print(f"  Treatment: {treatment_count} ({treatment_count/len(user_ids):.1%})")
            
        def test_random_assignment_weighted(self, randomization_engine):
            """Test weighted random assignment."""
            user_ids = [f"user_{i}" for i in range(10000)]
            variants = ["control", "treatment_a", "treatment_b"]
            weights = [0.4, 0.3, 0.3]  # 40%/30%/30% split
            
            # Assign users
            assignments = randomization_engine.assign_users(
                user_ids=user_ids,
                variants=variants,
                weights=weights
            )
            
            # Count assignments
            from collections import Counter
            counts = Counter(assignments.values())
            
            # Check proportions
            for variant, expected_weight in zip(variants, weights):
                actual_proportion = counts.get(variant, 0) / len(user_ids)
                assert abs(actual_proportion - expected_weight) < 0.02  # Within 2%
                
            print(f"\nRandom assignment (weighted):")
            for variant in variants:
                count = counts.get(variant, 0)
                proportion = count / len(user_ids)
                expected = weights[variants.index(variant)]
                print(f"  {variant}: {count} ({proportion:.1%}, expected {expected:.1%})")
                
        def test_deterministic_assignment(self, randomization_engine):
            """Test deterministic assignment (same user always gets same variant)."""
            user_ids = [f"user_{i}" for i in range(100)]
            
            # First assignment
            assignments1 = randomization_engine.assign_users(
                user_ids=user_ids,
                variants=["control", "treatment"],
                weights=[0.5, 0.5]
            )
            
            # Second assignment (should be identical)
            assignments2 = randomization_engine.assign_users(
                user_ids=user_ids,
                variants=["control", "treatment"],
                weights=[0.5, 0.5]
            )
            
            # Should be deterministic
            for user_id in user_ids:
                assert assignments1[user_id] == assignments2[user_id]
                
            print(f"\nDeterministic assignment:")
            print(f"  All {len(user_ids)} users got same variant in both assignments")
            
        def test_stratified_randomization(self, randomization_engine):
            """Test stratified randomization."""
            users = []
            for i in range(1000):
                # Create users with different segments
                segment = "high_value" if i % 3 == 0 else "low_value"
                users.append({
                    "user_id": f"user_{i}",
                    "segment": segment,
                    "signup_date": f"2024-{(i % 12) + 1:02d}-01"
                })
            
            # Stratified assignment
            assignments = randomization_engine.stratified_assignment(
                users=users,
                variants=["control", "treatment"],
                weights=[0.5, 0.5],
                strata_columns=["segment", "signup_date"]
            )
            
            # Check balance within strata
            from collections import defaultdict
            
            stratum_counts = defaultdict(lambda: defaultdict(int))
            
            for user, variant in assignments.items():
                # Find user data
                user_data = next(u for u in users if u["user_id"] == user)
                stratum_key = f"{user_data['segment']}_{user_data['signup_date']}"
                stratum_counts[stratum_key][variant] += 1
            
            # Within each stratum, should be balanced
            for stratum, counts in stratum_counts.items():
                total = sum(counts.values())
                if total > 1:  # Only check strata with multiple users
                    control_prop = counts.get("control", 0) / total
                    assert 0.4 <= control_prop <= 0.6  # Balanced within stratum
                    
            print(f"\nStratified randomization:")
            print(f"  Number of strata: {len(stratum_counts)}")
            print(f"  Users assigned: {len(assignments)}")
            
        def test_experiment_overlap_prevention(self, randomization_engine):
            """Test prevention of experiment overlap."""
            user_ids = [f"user_{i}" for i in range(1000)]
            
            # Assign to first experiment
            experiment1_assignments = randomization_engine.assign_users(
                user_ids=user_ids,
                variants=["control", "treatment"],
                weights=[0.5, 0.5],
                experiment_id="exp_1"
            )
            
            # Assign to second experiment, excluding users in first experiment
            experiment2_users = user_ids[:500]  # Overlap with first 500 users
            experiment2_assignments = randomization_engine.assign_users(
                user_ids=experiment2_users,
                variants=["variant_a", "variant_b"],
                weights=[0.5, 0.5],
                experiment_id="exp_2",
                exclude_experiments=["exp_1"]  # Don't assign users already in exp_1
            )
            
            # Users in exp_1 should not be in exp_2
            for user_id in user_ids[:500]:
                if user_id in experiment1_assignments:
                    assert user_id not in experiment2_assignments
                    
            print(f"\nExperiment overlap prevention:")
            print(f"  Experiment 1: {len(experiment1_assignments)} users")
            print(f"  Experiment 2: {len(experiment2_assignments)} users (no overlap with exp_1)")
            
    # Test Group 4: Bias Detection
    class TestBiasDetection:
        """Tests for bias detection in A/B tests."""
        
        def test_selection_bias_detection(self, bias_detector, simulated_conversion_data):
            """Test detection of selection bias."""
            control_data = simulated_conversion_data["control"]
            treatment_data = simulated_conversion_data["treatment"]
            
            # Simulate selection bias by creating imbalanced segments
            # Control has more "high_value" users
            control_segments = ["high_value"] * 3000 + ["low_value"] * 2000
            treatment_segments = ["high_value"] * 2000 + ["low_value"] * 3000
            
            # Check for selection bias
            bias_result = bias_detector.detect_selection_bias(
                control_segments=control_segments,
                treatment_segments=treatment_segments
            )
            
            # Should detect imbalance
            assert bias_result["has_bias"] is True
            assert bias_result["imbalance_metric"] > 0.1  # Significant imbalance
            
            print(f"\nSelection bias detection:")
            print(f"  Control segments: {len(control_segments)} users")
            print(f"  Treatment segments: {len(treatment_segments)} users")
            print(f"  Imbalance metric: {bias_result['imbalance_metric']:.3f}")
            print(f"  Has bias: {bias_result['has_bias']}")
            
        def test_time_based_bias_detection(self, bias_detector):
            """Test detection of time-based bias."""
            np.random.seed(42)
            
            # Simulate data with time trend (improvement over time)
            days = 30
            users_per_day = 100
            
            control_conversions = []
            treatment_conversions = []
            timestamps = []
            
            for day in range(days):
                # Control: constant 10% conversion
                control_day = np.random.binomial(1, 0.10, size=users_per_day)
                control_conversions.extend(control_day)
                
                # Treatment: starts at 10%, increases to 12% over time
                treatment_rate = 0.10 + (day / days) * 0.02
                treatment_day = np.random.binomial(1, treatment_rate, size=users_per_day)
                treatment_conversions.extend(treatment_day)
                
                timestamps.extend([day] * users_per_day)
            
            # Detect time-based bias
            bias_result = bias_detector.detect_time_bias(
                timestamps=timestamps,
                conversions=control_conversions + treatment_conversions,
                variants=["control"] * len(control_conversions) + ["treatment"] * len(treatment_conversions)
            )
            
            # Should detect time trend
            print(f"\nTime-based bias detection:")
            print(f"  Time trend p-value: {bias_result['time_trend_p_value']:.4f}")
            print(f"  Has time bias: {bias_result['has_time_bias']}")
            print(f"  Recommendation: {bias_result['recommendation']}")
            
        def test_novelty_effect_detection(self, bias_detector):
            """Test detection of novelty effect."""
            np.random.seed(42)
            
            # Simulate novelty effect: high initial engagement that decays
            days = 30
            users_per_day = 100
            
            treatment_conversions = []
            
            for day in range(days):
                # Novelty effect: decays from 15% to 12%
                if day < 7:  # First week: novelty effect
                    rate = 0.15 * (1 - day/14)  # Decay over first two weeks
                else:  # After novelty wears off
                    rate = 0.12
                
                day_conversions = np.random.binomial(1, rate, size=users_per_day)
                treatment_conversions.extend(day_conversions)
            
            # Detect novelty effect
            bias_result = bias_detector.detect_novelty_effect(
                timestamps=list(range(days)) * users_per_day,
                conversions=treatment_conversions,
                variant="treatment"
            )
            
            # Should detect decay pattern
            print(f"\nNovelty effect detection:")
            print(f"  Decay rate: {bias_result['decay_rate']:.4f}")
            print(f"  Has novelty effect: {bias_result['has_novelty_effect']}")
            print(f"  Stable after days: {bias_result['stable_after_days']}")
            
        def test_sample_ratio_mismatch(self, bias_detector, randomization_engine):
            """Test detection of sample ratio mismatch (SRM)."""
            np.random.seed(42)
            
            # Simulate SRM: expected 50/50 split, actual 45/55
            expected_weights = [0.5, 0.5]
            
            # Generate assignments with SRM
            n_users = 10000
            control_users = int(n_users * 0.45)  # 45% instead of 50%
            treatment_users = n_users - control_users
            
            assignments = {}
            for i in range(control_users):
                assignments[f"user_{i}"] = "control"
            for i in range(control_users, n_users):
                assignments[f"user_{i}"] = "treatment"
            
            # Detect SRM
            srm_result = bias_detector.detect_sample_ratio_mismatch(
                assignments=assignments,
                expected_weights=expected_weights,
                variants=["control", "treatment"]
            )
            
            # Should detect significant SRM
            assert srm_result["has_srm"] is True
            assert srm_result["p_value"] < 0.05
            
            print(f"\nSample ratio mismatch detection:")
            print(f"  Expected split: 50/50")
            print(f"  Actual split: {srm_result['actual_proportions']}")
            print(f"  Chi2 statistic: {srm_result['chi2_statistic']:.3f}")
            print(f"  P-value: {srm_result['p_value']:.6f}")
            print(f"  Has SRM: {srm_result['has_srm']}")
            
        def test_metric_imbalance_detection(self, bias_detector):
            """Test detection of metric imbalance (pre-experiment)."""
            np.random.seed(42)
            
            # Pre-experiment metrics (should be similar between groups)
            control_pre_metrics = {
                "active_days": np.random.normal(15, 5, 1000),
                "previous_purchases": np.random.poisson(2, 1000),
                "session_duration": np.random.exponential(300, 1000)
            }
            
            # Treatment has slightly different pre-metrics (imbalance)
            treatment_pre_metrics = {
                "active_days": np.random.normal(16, 5, 1000),  # Slightly higher
                "previous_purchases": np.random.poisson(2.2, 1000),  # Slightly higher
                "session_duration": np.random.exponential(320, 1000)  # Slightly higher
            }
            
            # Detect metric imbalance
            imbalance_result = bias_detector.detect_metric_imbalance(
                control_metrics=control_pre_metrics,
                treatment_metrics=treatment_pre_metrics
            )
            
            # Should detect some imbalance
            assert len(imbalance_result["imbalanced_metrics"]) > 0
            
            print(f"\nMetric imbalance detection:")
            print(f"  Metrics tested: {len(imbalance_result['all_metrics'])}")
            print(f"  Imbalanced metrics: {len(imbalance_result['imbalanced_metrics'])}")
            for metric in imbalance_result["imbalanced_metrics"][:3]:  # Show top 3
                print(f"    {metric['name']}: p={metric['p_value']:.4f}")
                
    # Test Group 5: Result Interpretation
    class TestResultInterpretation:
        """Tests for result interpretation."""
        
        def test_business_significance_interpretation(self, result_interpreter):
            """Test interpretation of business significance."""
            # Statistical results
            statistical_result = {
                "significant": True,
                "p_value": 0.01,
                "effect_size": 0.15,  # 15% improvement
                "ci_lower": 0.05,
                "ci_upper": 0.25
            }
            
            # Business context
            business_context = {
                "min_business_impact": 0.10,  # 10% improvement needed
                "implementation_cost": 50000,
                "expected_annual_revenue_impact": 100000,
                "risk_level": "medium"
            }
            
            # Interpret results
            interpretation = result_interpreter.interpret_results(
                statistical_result=statistical_result,
                business_context=business_context
            )
            
            # Verify interpretation
            assert interpretation["statistically_significant"] is True
            assert interpretation["business_significant"] is True
            assert interpretation["recommendation"] in ["implement", "consider_implementing"]
            
            # Calculate ROI
            roi = (business_context["expected_annual_revenue_impact"] - 
                   business_context["implementation_cost"]) / business_context["implementation_cost"]
            
            print(f"\nBusiness significance interpretation:")
            print(f"  Statistical significance: {interpretation['statistically_significant']}")
            print(f"  Business significance: {interpretation['business_significant']}")
            print(f"  Effect size: {statistical_result['effect_size']:.1%}")
            print(f"  Min business impact: {business_context['min_business_impact']:.1%}")
            print(f"  Expected ROI: {roi:.1%}")
            print(f"  Recommendation: {interpretation['recommendation']}")
            
        def test_non_significant_result_interpretation(self, result_interpreter):
            """Test interpretation of non-significant results."""
            statistical_result = {
                "significant": False,
                "p_value": 0.15,
                "effect_size": 0.08,  # 8% improvement
                "ci_lower": -0.02,
                "ci_upper": 0.18
            }
            
            interpretation = result_interpreter.interpret_results(
                statistical_result=statistical_result,
                business_context={"min_business_impact": 0.10}
            )
            
            # Should recommend not implementing (confidence interval includes zero or negative)
            assert interpretation["recommendation"] in ["do_not_implement", "gather_more_data"]
            
            print(f"\nNon-significant result interpretation:")
            print(f"  Significant: {statistical_result['significant']}")
            print(f"  P-value: {statistical_result['p_value']:.3f}")
            print(f"  Effect size: {statistical_result['effect_size']:.1%}")
            print(f"  CI: [{statistical_result['ci_lower']:.3f}, {statistical_result['ci_upper']:.3f}]")
            print(f"  Recommendation: {interpretation['recommendation']}")
            
        def test_harmful_result_interpretation(self, result_interpreter):
            """Test interpretation of harmful results."""
            statistical_result = {
                "significant": True,
                "p_value": 0.02,
                "effect_size": -0.05,  # 5% decrease
                "ci_lower": -0.09,
                "ci_upper": -0.01
            }
            
            interpretation = result_interpreter.interpret_results(
                statistical_result=statistical_result
            )
            
            # Should clearly recommend against implementation
            assert interpretation["recommendation"] == "do_not_implement"
            assert interpretation["harmful"] is True
            
            print(f"\nHarmful result interpretation:")
            print(f"  Significant: {statistical_result['significant']}")
            print(f"  Effect size: {statistical_result['effect_size']:.1%} (negative)")
            print(f"  Harmful: {interpretation['harmful']}")
            print(f"  Recommendation: {interpretation['recommendation']}")
            
        def test_guardrail_metric_interpretation(self, result_interpreter):
            """Test interpretation with guardrail metrics."""
            primary_result = {
                "significant": True,
                "p_value": 0.01,
                "effect_size": 0.12,
                "metric": "conversion_rate"
            }
            
            guardrail_results = {
                "churn_rate": {
                    "significant": True,
                    "p_value": 0.03,
                    "effect_size": 0.02,  # 2% increase in churn
                    "ci_lower": 0.005,
                    "ci_upper": 0.035
                },
                "customer_satisfaction": {
                    "significant": False,
                    "p_value": 0.20,
                    "effect_size": -0.01,
                    "ci_lower": -0.03,
                    "ci_upper": 0.01
                }
            }
            
            interpretation = result_interpreter.interpret_with_guardrails(
                primary_result=primary_result,
                guardrail_results=guardrail_results,
                guardrail_limits={"churn_rate": 0.01}  # Max 1% increase allowed
            )
            
            # Should flag guardrail violation
            assert interpretation["guardrail_violations"] == ["churn_rate"]
            assert interpretation["recommendation"] == "do_not_implement"  # Due to churn increase
            
            print(f"\nGuardrail metric interpretation:")
            print(f"  Primary metric improvement: {primary_result['effect_size']:.1%}")
            print(f"  Guardrail violations: {interpretation['guardrail_violations']}")
            print(f"  Recommendation: {interpretation['recommendation']}")
            
        def test_subgroup_analysis_interpretation(self, result_interpreter):
            """Test interpretation of subgroup analysis."""
            overall_result = {
                "significant": True,
                "effect_size": 0.10,
                "p_value": 0.02
            }
            
            subgroup_results = {
                "high_value_users": {
                    "significant": True,
                    "effect_size": 0.20,
                    "p_value": 0.01,
                    "sample_size": 2000
                },
                "low_value_users": {
                    "significant": False,
                    "effect_size": 0.02,
                    "p_value": 0.40,
                    "sample_size": 3000
                },
                "new_users": {
                    "significant": True,
                    "effect_size": 0.15,
                    "p_value": 0.03,
                    "sample_size": 1500
                }
            }
            
            interpretation = result_interpreter.interpret_subgroup_analysis(
                overall_result=overall_result,
                subgroup_results=subgroup_results
            )
            
            # Should identify heterogeneous treatment effects
            assert len(interpretation["significant_subgroups"]) > 0
            assert interpretation["heterogeneous_effect"] is True
            
            print(f"\nSubgroup analysis interpretation:")
            print(f"  Overall effect: {overall_result['effect_size']:.1%}")
            print(f"  Significant subgroups: {len(interpretation['significant_subgroups'])}")
            print(f"  Heterogeneous effect: {interpretation['heterogeneous_effect']}")
            print(f"  Recommendation: {interpretation['recommendation']}")
            
    # Test Group 6: Confidence Interval Calculation
    class TestConfidenceIntervals:
        """Tests for confidence interval calculation."""
        
        def test_proportion_confidence_interval(self, statistical_test):
            """Test confidence interval for proportions."""
            # Sample data
            successes = 120
            trials = 1000
            confidence_level = 0.95
            
            # Calculate CI
            ci = statistical_test.proportion_confidence_interval(
                successes=successes,
                trials=trials,
                confidence_level=confidence_level
            )
            
            # Verify calculation
            assert "point_estimate" in ci
            assert "ci_lower" in ci
            assert "ci_upper" in ci
            assert "confidence_level" in ci
            
            point_estimate = successes / trials
            assert ci["point_estimate"] == pytest.approx(point_estimate, rel=1e-3)
            
            # CI should contain point estimate
            assert ci["ci_lower"] < point_estimate < ci["ci_upper"]
            
            print(f"\nProportion confidence interval:")
            print(f"  Successes: {successes} / {trials}")
            print(f"  Point estimate: {ci['point_estimate']:.3%}")
            print(f"  {confidence_level:.0%} CI: [{ci['ci_lower']:.3%}, {ci['ci_upper']:.3%}]")
            
        def test_mean_confidence_interval(self, statistical_test):
            """Test confidence interval for means."""
            np.random.seed(42)
            
            # Sample data
            data = np.random.normal(50, 10, 1000)
            confidence_level = 0.95
            
            # Calculate CI
            ci = statistical_test.mean_confidence_interval(
                data=data,
                confidence_level=confidence_level
            )
            
            # Verify calculation
            assert "point_estimate" in ci
            assert "ci_lower" in ci
            assert "ci_upper" in ci
            
            point_estimate = np.mean(data)
            assert ci["point_estimate"] == pytest.approx(point_estimate, rel=1e-3)
            
            print(f"\nMean confidence interval:")
            print(f"  Sample size: {len(data)}")
            print(f"  Point estimate (mean): {ci['point_estimate']:.2f}")
            print(f"  {confidence_level:.0%} CI: [{ci['ci_lower']:.2f}, {ci['ci_upper']:.2f}]")
            
        def test_difference_confidence_interval(self, statistical_test, simulated_conversion_data):
            """Test confidence interval for difference between groups."""
            control_data = simulated_conversion_data["control"]
            treatment_data = simulated_conversion_data["treatment"]
            
            # Calculate CI for difference in proportions
            ci = statistical_test.difference_confidence_interval(
                successes1=sum(control_data["conversions"]),
                trials1=len(control_data["conversions"]),
                successes2=sum(treatment_data["conversions"]),
                trials2=len(treatment_data["conversions"]),
                confidence_level=0.95
            )
            
            # Verify calculation
            assert "point_estimate" in ci
            assert "ci_lower" in ci
            assert "ci_upper" in ci
            assert "relative_improvement" in ci
            
            # Point estimate should be positive (treatment better)
            assert ci["point_estimate"] > 0
            
            # If CI doesn't include 0, difference is significant
            significant = ci["ci_lower"] > 0 or ci["ci_upper"] < 0
            assert significant is True  # Should be significant with our data
            
            print(f"\nDifference confidence interval:")
            print(f"  Absolute difference: {ci['point_estimate']:.3%}")
            print(f"  Relative improvement: {ci['relative_improvement']:.1%}")
            print(f"  95% CI: [{ci['ci_lower']:.3%}, {ci['ci_upper']:.3%}]")
            print(f"  Significant (CI excludes 0): {significant}")
            
        def test_ratio_confidence_interval(self, statistical_test):
            """Test confidence interval for ratios (relative improvement)."""
            # Sample data
            control_rate = 0.10
            treatment_rate = 0.12
            n_control = 5000
            n_treatment = 5000
            
            # Calculate CI for ratio
            ci = statistical_test.ratio_confidence_interval(
                rate1=control_rate,
                n1=n_control,
                rate2=treatment_rate,
                n2=n_treatment,
                confidence_level=0.95
            )
            
            # Verify calculation
            assert "point_estimate" in ci  # Ratio treatment/control
            assert "ci_lower" in ci
            assert "ci_upper" in ci
            
            point_estimate = treatment_rate / control_rate
            assert ci["point_estimate"] == pytest.approx(point_estimate, rel=1e-3)
            
            # Ratio > 1 means treatment better
            assert ci["point_estimate"] > 1.0
            
            print(f"\nRatio confidence interval:")
            print(f"  Control rate: {control_rate:.2%}")
            print(f"  Treatment rate: {treatment_rate:.2%}")
            print(f"  Ratio (treatment/control): {ci['point_estimate']:.3f}")
            print(f"  95% CI: [{ci['ci_lower']:.3f}, {ci['ci_upper']:.3f}]")
            print(f"  Relative improvement: {(ci['point_estimate'] - 1) * 100:.1f}%")
            
        def test_bootstrap_confidence_interval(self, statistical_test):
            """Test bootstrap confidence interval for complex metrics."""
            np.random.seed(42)
            
            # Simulated revenue data (non-normal distribution)
            control_revenue = np.random.exponential(50, 1000)
            treatment_revenue = np.random.exponential(55, 1000)
            
            # Complex metric: (mean revenue) / (95th percentile latency)
            # This is a made-up metric to demonstrate bootstrap
            
            # Calculate metric for each group
            def calculate_metric(data):
                # Simplified: just use mean for demonstration
                return np.mean(data)
            
            # Bootstrap CI
            ci = statistical_test.bootstrap_confidence_interval(
                data1=control_revenue,
                data2=treatment_revenue,
                statistic_fn=calculate_metric,
                confidence_level=0.95,
                n_bootstrap=1000
            )
            
            # Verify calculation
            assert "point_estimate_diff" in ci
            assert "ci_lower" in ci
            assert "ci_upper" in ci
            
            print(f"\nBootstrap confidence interval:")
            print(f"  Control metric: {calculate_metric(control_revenue):.2f}")
            print(f"  Treatment metric: {calculate_metric(treatment_revenue):.2f}")
            print(f"  Difference: {ci['point_estimate_diff']:.2f}")
            print(f"  95% CI: [{ci['ci_lower']:.2f}, {ci['ci_upper']:.2f}]")
            
    # Test Group 7: Multi-Variate Testing
    class TestMultiVariateTesting:
        """Tests for multi-variate testing."""
        
        def test_factorial_design_generation(self, multivariate_test):
            """Test generation of factorial design."""
            # Factors to test
            factors = {
                "button_color": ["blue", "green", "red"],
                "button_size": ["small", "large"],
                "headline_text": ["default", "benefit", "urgency"]
            }
            
            # Generate full factorial design
            design = multivariate_test.generate_factorial_design(
                factors=factors,
                design_type="full"
            )
            
            # Verify design
            assert len(design["combinations"]) == 3 * 2 * 3  # 18 combinations
            assert "factor_matrix" in design
            assert "design_matrix" in design
            
            print(f"\nFactorial design generation:")
            print(f"  Factors: {list(factors.keys())}")
            print(f"  Full factorial combinations: {len(design['combinations'])}")
            print(f"  Sample combination: {design['combinations'][0]}")
            
        def test_fractional_factorial_design(self, multivariate_test):
            """Test fractional factorial design (reduced combinations)."""
            factors = {
                "layout": ["A", "B", "C"],
                "color_scheme": ["light", "dark"],
                "typography": ["serif", "sans_serif"],
                "spacing": ["compact", "comfortable"],
                "imagery": ["product", "lifestyle"]
            }
            
            # Generate fractional factorial (reduces combinations)
            design = multivariate_test.generate_factorial_design(
                factors=factors,
                design_type="fractional",
                resolution=4  # Can estimate main effects and 2-way interactions
            )
            
            # Should have fewer combinations than full factorial
            full_size = 3 * 2 * 2 * 2 * 2  # 48 combinations
            assert len(design["combinations"]) < full_size
            
            print(f"\nFractional factorial design:")
            print(f"  Factors: {len(factors)}")
            print(f"  Full factorial size: {full_size}")
            print(f"  Fractional factorial size: {len(design['combinations'])}")
            print(f"  Reduction: {(1 - len(design['combinations'])/full_size)*100:.0f}%")
            
        def test_interaction_effect_estimation(self, multivariate_test):
            """Test estimation of interaction effects."""
            np.random.seed(42)
            
            # Simulated data with interaction
            n = 1000
            
            # Factors
            factor_a = np.random.choice([0, 1], n)  # 0=control, 1=treatment
            factor_b = np.random.choice([0, 1], n)  # 0=simple, 1=advanced
            
            # Response with interaction
            # Base conversion: 10%
            # Factor A effect: +2%
            # Factor B effect: +1%
            # Interaction: +3% when both A and B are 1
            base_rate = 0.10
            response_prob = base_rate + 0.02*factor_a + 0.01*factor_b + 0.03*factor_a*factor_b
            response = np.random.binomial(1, response_prob, n)
            
            # Estimate effects
            effects = multivariate_test.estimate_effects(
                factors={"A": factor_a, "B": factor_b},
                response=response
            )
            
            # Verify effects
            assert "main_effects" in effects
            assert "interaction_effects" in effects
            
            # Should detect interaction
            assert len(effects["interaction_effects"]) > 0
            
            print(f"\nInteraction effect estimation:")
            print(f"  Main effects: {len(effects['main_effects'])}")
            print(f"  Interaction effects: {len(effects['interaction_effects'])}")
            
            for effect_name, effect in list(effects["main_effects"].items())[:2]:
                print(f"    {effect_name}: {effect['estimate']:.3f} (p={effect['p_value']:.3f})")
                
        def test_response_surface_methodology(self, multivariate_test):
            """Test response surface methodology for optimization."""
            # Simulate response surface (quadratic)
            def true_response(x1, x2):
                return 10 + 2*x1 + 3*x2 - 1.5*x1**2 - 2*x2**2 + 1.2*x1*x2
            
            # Experimental design points
            design_points = [
                {"x1": -1, "x2": -1},
                {"x1": 0, "x2": -1},
                {"x1": 1, "x2": -1},
                {"x1": -1, "x2": 0},
                {"x1": 0, "x2": 0},
                {"x1": 1, "x2": 0},
                {"x1": -1, "x2": 1},
                {"x1": 0, "x2": 1},
                {"x1": 1, "x2": 1},
            ]
            
            # Add noise to responses
            np.random.seed(42)
            responses = [true_response(p["x1"], p["x2"]) + np.random.normal(0, 0.5) 
                        for p in design_points]
            
            # Fit response surface
            surface = multivariate_test.fit_response_surface(
                design_points=design_points,
                responses=responses,
                degree=2  # Quadratic
            )
            
            # Find optimum
            optimum = multivariate_test.find_optimum(surface)
            
            # Verify optimum
            assert "optimal_point" in optimum
            assert "predicted_response" in optimum
            
            print(f"\nResponse surface methodology:")
            print(f"  Design points: {len(design_points)}")
            print(f"  Model R-squared: {surface['r_squared']:.3f}")
            print(f"  Optimal point: {optimum['optimal_point']}")
            print(f"  Predicted optimal response: {optimum['predicted_response']:.2f}")
            
    # Test Group 8: Bandit Algorithm Testing
    class TestBanditAlgorithms:
        """Tests for bandit algorithms."""
        
        def test_epsilon_greedy_bandit(self, bandit_algorithm):
            """Test epsilon-greedy bandit algorithm."""
            np.random.seed(42)
            
            # True conversion rates for 3 variants
            true_rates = [0.10, 0.12, 0.15]  # Variant 2 is best
            
            # Run bandit
            n_trials = 10000
            epsilon = 0.1  # 10% exploration
            
            results = bandit_algorithm.run_epsilon_greedy(
                true_rates=true_rates,
                n_trials=n_trials,
                epsilon=epsilon
            )
            
            # Verify results
            assert "choices" in results
            assert "rewards" in results
            assert "estimated_rates" in results
            assert "regret" in results
            
            # Should converge to best variant
            last_100_choices = results["choices"][-100:]
            best_variant = np.argmax(true_rates)
            best_choice_ratio = sum(1 for c in last_100_choices if c == best_variant) / 100
            
            assert best_choice_ratio > 0.8  # Should be mostly choosing best variant
            
            print(f"\nEpsilon-greedy bandit:")
            print(f"  True rates: {true_rates}")
            print(f"  Estimated rates: {[round(r, 3) for r in results['estimated_rates']]}")
            print(f"  Final choice ratio (best variant): {best_choice_ratio:.1%}")
            print(f"  Cumulative regret: {results['regret'][-1]:.1f}")
            
        def test_thompson_sampling_bandit(self, bandit_algorithm):
            """Test Thompson sampling bandit algorithm."""
            np.random.seed(42)
            
            # True conversion rates
            true_rates = [0.08, 0.11, 0.09, 0.13]  # Variant 3 is best
            
            # Run Thompson sampling
            results = bandit_algorithm.run_thompson_sampling(
                true_rates=true_rates,
                n_trials=5000
            )
            
            # Verify results
            best_variant = np.argmax(true_rates)
            last_100_choices = results["choices"][-100:]
            best_choice_ratio = sum(1 for c in last_100_choices if c == best_variant) / 100
            
            # Thompson sampling should converge quickly
            assert best_choice_ratio > 0.85
            
            print(f"\nThompson sampling bandit:")
            print(f"  True rates: {true_rates}")
            print(f"  Final choice ratio (best variant): {best_choice_ratio:.1%}")
            print(f"  Cumulative regret: {results['regret'][-1]:.1f}")
            
        def test_ucb_bandit(self, bandit_algorithm):
            """Test Upper Confidence Bound (UCB) bandit algorithm."""
            np.random.seed(42)
            
            true_rates = [0.10, 0.15, 0.12]  # Variant 1 is best
            
            results = bandit_algorithm.run_ucb(
                true_rates=true_rates,
                n_trials=3000,
                exploration_param=2.0
            )
            
            best_variant = np.argmax(true_rates)
            last_100_choices = results["choices"][-100:]
            best_choice_ratio = sum(1 for c in last_100_choices if c == best_variant) / 100
            
            assert best_choice_ratio > 0.8
            
            print(f"\nUCB bandit:")
            print(f"  True rates: {true_rates}")
            print(f"  Final choice ratio (best variant): {best_choice_ratio:.1%}")
            print(f"  Cumulative regret: {results['regret'][-1]:.1f}")
            
        def test_contextual_bandit(self, bandit_algorithm):
            """Test contextual bandit with user features."""
            np.random.seed(42)
            
            # Simulate user contexts
            n_users = 1000
            n_features = 5
            n_actions = 3
            
            # User features
            contexts = np.random.normal(0, 1, (n_users, n_features))
            
            # True reward function for each action
            true_weights = np.random.normal(0, 1, (n_actions, n_features))
            
            # Generate rewards
            def get_reward(context, action):
                expected = np.dot(context, true_weights[action])
                prob = 1 / (1 + np.exp(-expected))  # Logistic
                return np.random.binomial(1, prob)
            
            # Run contextual bandit
            results = bandit_algorithm.run_contextual_bandit(
                contexts=contexts,
                get_reward_fn=get_reward,
                n_actions=n_actions,
                algorithm="linucb"
            )
            
            # Calculate average reward
            avg_reward = np.mean(results["rewards"])
            
            print(f"\nContextual bandit:")
            print(f"  Users: {n_users}")
            print(f"  Actions: {n_actions}")
            print(f"  Features: {n_features}")
            print(f"  Average reward: {avg_reward:.3f}")
            
        def test_bandit_vs_ab_test_comparison(self, bandit_algorithm):
            """Compare bandit algorithms vs traditional A/B testing."""
            np.random.seed(42)
            
            true_rates = [0.10, 0.12, 0.11]  # Small differences
            
            # Run bandit
            bandit_results = bandit_algorithm.run_thompson_sampling(
                true_rates=true_rates,
                n_trials=10000
            )
            
            # Simulate A/B test (equal allocation for 5000 trials, then choose best)
            n_trials = 10000
            explore_trials = 5000
            
            # Equal exploration phase
            ab_choices = []
            ab_rewards = []
            
            for t in range(n_trials):
                if t < explore_trials:
                    # Equal allocation
                    choice = t % len(true_rates)
                else:
                    # Choose best based on exploration phase
                    # In reality, would use statistical test
                    choice = 1  # Assume we correctly identify best variant
                
                reward = np.random.binomial(1, true_rates[choice])
                ab_choices.append(choice)
                ab_rewards.append(reward)
            
            # Calculate cumulative rewards
            bandit_cumulative = np.cumsum(bandit_results["rewards"])
            ab_cumulative = np.cumsum(ab_rewards)
            
            # Bandit should have higher cumulative reward (less regret)
            bandit_total = bandit_cumulative[-1]
            ab_total = ab_cumulative[-1]
            
            print(f"\nBandit vs A/B test comparison:")
            print(f"  True rates: {true_rates}")
            print(f"  Total trials: {n_trials}")
            print(f"  Bandit total reward: {bandit_total}")
            print(f"  A/B test total reward: {ab_total}")
            print(f"  Bandit improvement: {(bandit_total - ab_total)/ab_total:.1%}")
            
    # Test Group 9: Personalization Testing
    class TestPersonalization:
        """Tests for personalization and segmentation."""
        
        def test_segmentation_analysis(self, personalization_engine):
            """Test segmentation analysis for personalization."""
            np.random.seed(42)
            
            # Simulated user data with segments
            n_users = 5000
            
            # User features
            segments = np.random.choice(["new", "active", "churned"], n_users, p=[0.3, 0.5, 0.2])
            age_groups = np.random.choice(["18-24", "25-34", "35-44", "45+"], n_users)
            geos = np.random.choice(["us", "eu", "asia"], n_users, p=[0.5, 0.3, 0.2])
            
            # Treatment effect varies by segment
            base_rate = 0.10
            
            conversion_probs = []
            for i in range(n_users):
                if segments[i] == "new":
                    # Treatment works well for new users
                    prob = base_rate + 0.05 if i % 2 == 0 else base_rate
                elif segments[i] == "active":
                    # Moderate effect for active users
                    prob = base_rate + 0.02 if i % 2 == 0 else base_rate
                else:  # churned
                    # No effect for churned users
                    prob = base_rate if i % 2 == 0 else base_rate
                conversion_probs.append(prob)
            
            conversions = np.random.binomial(1, conversion_probs)
            
            # Analyze segments
            segment_results = personalization_engine.analyze_segments(
                segments=segments,
                treatments=np.array([i % 2 for i in range(n_users)]),  # 0=control, 1=treatment
                outcomes=conversions
            )
            
            # Should find heterogeneous effects
            assert len(segment_results["significant_segments"]) > 0
            
            print(f"\nSegmentation analysis:")
            print(f"  Total segments analyzed: {len(segment_results['segment_effects'])}")
            print(f"  Significant segments: {len(segment_results['significant_segments'])}")
            
            for segment in segment_results["significant_segments"][:3]:
                print(f"    {segment['segment']}: effect={segment['effect_size']:.3f}, "
                      f"p={segment['p_value']:.3f}")
                
        def test_recommendation_personalization(self, personalization_engine):
            """Test personalized recommendations."""
            np.random.seed(42)
            
            # Simulate user-item matrix
            n_users = 100
            n_items = 50
            
            # True preferences (latent factors)
            n_factors = 5
            user_factors = np.random.normal(0, 1, (n_users, n_factors))
            item_factors = np.random.normal(0, 1, (n_items, n_factors))
            
            # Ratings matrix (with noise)
            true_ratings = user_factors @ item_factors.T
            noise = np.random.normal(0, 0.5, (n_users, n_items))
            observed_ratings = true_ratings + noise
            
            # Train personalized model
            model = personalization_engine.train_recommendation_model(
                ratings=observed_ratings,
                algorithm="matrix_factorization",
                n_factors=n_factors
            )
            
            # Generate recommendations
            user_id = 0
            recommendations = personalization_engine.generate_recommendations(
                model=model,
                user_id=user_id,
                n_recommendations=5
            )
            
            # Verify recommendations
            assert len(recommendations) == 5
            assert all(0 <= item_id < n_items for item_id, _ in recommendations)
            
            print(f"\nPersonalized recommendations:")
            print(f"  Users: {n_users}")
            print(f"  Items: {n_items}")
            print(f"  Latent factors: {n_factors}")
            print(f"  Recommendations for user {user_id}: {recommendations}")
            
        def test_bandit_personalization(self, personalization_engine):
            """Test bandit-based personalization."""
            np.random.seed(42)
            
            # Simulate contextual bandit personalization
            n_users = 1000
            n_rounds = 100
            
            total_rewards = 0
            
            for round in range(n_rounds):
                # Sample batch of users
                batch_size = 100
                user_features = np.random.normal(0, 1, (batch_size, 3))
                
                # Get personalized actions
                actions = personalization_engine.get_personalized_actions(
                    user_features=user_features,
                    context=round
                )
                
                # Simulate rewards (higher for better personalization)
                rewards = np.random.binomial(1, 0.1 + 0.05 * (actions == 1))
                total_rewards += np.sum(rewards)
                
                # Update model
                personalization_engine.update_personalization_model(
                    user_features=user_features,
                    actions=actions,
                    rewards=rewards
                )
            
            avg_reward = total_rewards / (n_rounds * 100)
            
            print(f"\nBandit personalization:")
            print(f"  Users per round: 100")
            print(f"  Rounds: {n_rounds}")
            print(f"  Total rewards: {total_rewards}")
            print(f"  Average reward per user: {avg_reward:.3f}")
            
    # Test Group 10: ROI Comparison Testing
    class TestROIComparison:
        """Tests for ROI comparison in A/B tests."""
        
        def test_roi_calculation_simple(self, roi_comparator):
            """Test simple ROI calculation."""
            # Test parameters
            control_metrics = {
                "conversion_rate": 0.10,
                "average_order_value": 100.0,
                "customers_per_month": 10000
            }
            
            treatment_metrics = {
                "conversion_rate": 0.12,  # 20% improvement
                "average_order_value": 102.0,  # 2% increase
                "customers_per_month": 10000
            }
            
            # Costs
            implementation_cost = 50000
            monthly_maintenance = 1000
            
            # Calculate ROI
            roi_result = roi_comparator.calculate_roi(
                control_metrics=control_metrics,
                treatment_metrics=treatment_metrics,
                implementation_cost=implementation_cost,
                monthly_costs=monthly_maintenance,
                time_horizon_months=12
            )
            
            # Verify calculation
            assert "annual_incremental_revenue" in roi_result
            assert "annual_incremental_cost" in roi_result
            assert "roi_percentage" in roi_result
            assert "payback_period_months" in roi_result
            assert "npv" in roi_result
            
            # Should be positive ROI with these numbers
            assert roi_result["roi_percentage"] > 0
            
            print(f"\nSimple ROI calculation:")
            print(f"  Control revenue: ${roi_result['control_annual_revenue']:,.0f}")
            print(f"  Treatment revenue: ${roi_result['treatment_annual_revenue']:,.0f}")
            print(f"  Incremental revenue: ${roi_result['annual_incremental_revenue']:,.0f}")
            print(f"  ROI: {roi_result['roi_percentage']:.1%}")
            print(f"  Payback period: {roi_result['payback_period_months']:.1f} months")
            print(f"  NPV (12 months): ${roi_result['npv']:,.0f}")
            
        def test_roi_with_uncertainty(self, roi_comparator):
            """Test ROI calculation with uncertainty (confidence intervals)."""
            # Metrics with confidence intervals
            control_metrics = {
                "conversion_rate": {"point": 0.10, "ci_lower": 0.095, "ci_upper": 0.105},
                "average_order_value": {"point": 100.0, "ci_lower": 95.0, "ci_upper": 105.0}
            }
            
            treatment_metrics = {
                "conversion_rate": {"point": 0.12, "ci_lower": 0.115, "ci_upper": 0.125},
                "average_order_value": {"point": 102.0, "ci_lower": 97.0, "ci_upper": 107.0}
            }
            
            # Calculate ROI with uncertainty
            roi_result = roi_comparator.calculate_roi_with_uncertainty(
                control_metrics=control_metrics,
                treatment_metrics=treatment_metrics,
                implementation_cost=50000,
                monthly_costs=1000,
                time_horizon_months=12,
                n_simulations=1000
            )
            
            # Verify results include uncertainty
            assert "roi_distribution" in roi_result
            assert "roi_mean" in roi_result
            assert "roi_ci_lower" in roi_result
            assert "roi_ci_upper" in roi_result
            assert "probability_positive_roi" in roi_result
            
            print(f"\nROI calculation with uncertainty:")
            print(f"  ROI mean: {roi_result['roi_mean']:.1%}")
            print(f"  ROI 95% CI: [{roi_result['roi_ci_lower']:.1%}, {roi_result['roi_ci_upper']:.1%}]")
            print(f"  Probability of positive ROI: {roi_result['probability_positive_roi']:.1%}")
            
        def test_multi_variant_roi_comparison(self, roi_comparator):
            """Test ROI comparison across multiple variants."""
            # Multiple treatment variants
            variants = {
                "control": {
                    "conversion_rate": 0.10,
                    "avg_order_value": 100.0,
                    "implementation_cost": 0,
                    "monthly_cost": 0
                },
                "treatment_a": {
                    "conversion_rate": 0.11,
                    "avg_order_value": 101.0,
                    "implementation_cost": 20000,
                    "monthly_cost": 500
                },
                "treatment_b": {
                    "conversion_rate": 0.12,
                    "avg_order_value": 102.0,
                    "implementation_cost": 50000,
                    "monthly_cost": 1000
                },
                "treatment_c": {
                    "conversion_rate": 0.13,
                    "avg_order_value": 98.0,  # Lower AOV
                    "implementation_cost": 80000,
                    "monthly_cost": 1500
                }
            }
            
            # Compare ROI across variants
            comparison = roi_comparator.compare_variants_roi(
                variants=variants,
                monthly_customers=10000,
                time_horizon_months=12
            )
            
            # Verify comparison
            assert "roi_by_variant" in comparison
            assert "best_variant" in comparison
            assert "efficient_frontier" in comparison
            
            print(f"\nMulti-variant ROI comparison:")
            for variant, roi in comparison["roi_by_variant"].items():
                print(f"  {variant}: ROI = {roi['roi_percentage']:.1%}, "
                      f"NPV = ${roi['npv']:,.0f}")
            
            print(f"  Best variant: {comparison['best_variant']}")
            
        def test_risk_adjusted_roi(self, roi_comparator):
            """Test risk-adjusted ROI calculation."""
            # Metrics with risk information
            control = {
                "expected_revenue": 1000000,
                "revenue_std": 100000,  # Lower risk
                "cost": 0
            }
            
            treatment = {
                "expected_revenue": 1200000,  # Higher expected
                "revenue_std": 200000,  # Higher risk
                "cost": 100000
            }
            
            # Calculate risk-adjusted ROI
            risk_result = roi_comparator.calculate_risk_adjusted_roi(
                control=control,
                treatment=treatment,
                risk_free_rate=0.02,
                risk_aversion=2.0
            )
            
            # Verify risk adjustment
            assert "sharpe_ratio" in risk_result
            assert "certainty_equivalent" in risk_result
            assert "risk_adjusted_roi" in risk_result
            
            print(f"\nRisk-adjusted ROI:")
            print(f"  Expected ROI: {risk_result['expected_roi']:.1%}")
            print(f"  ROI volatility: {risk_result['roi_volatility']:.1%}")
            print(f"  Sharpe ratio: {risk_result['sharpe_ratio']:.2f}")
            print(f"  Risk-adjusted ROI: {risk_result['risk_adjusted_roi']:.1%}")
            
        def test_sensitivity_analysis(self, roi_comparator):
            """Test sensitivity analysis for ROI."""
            # Base case
            base_case = {
                "conversion_rate_improvement": 0.02,  # 2 percentage points
                "avg_order_value_change": 0.01,  # 1% increase
                "implementation_cost": 50000,
                "monthly_traffic": 10000
            }
            
            # Sensitivity parameters
            sensitivity_params = {
                "conversion_rate_improvement": {"min": 0.01, "max": 0.03, "step": 0.005},
                "avg_order_value_change": {"min": -0.02, "max": 0.04, "step": 0.01},
                "monthly_traffic": {"min": 5000, "max": 20000, "step": 5000}
            }
            
            # Perform sensitivity analysis
            sensitivity = roi_comparator.sensitivity_analysis(
                base_case=base_case,
                sensitivity_params=sensitivity_params
            )
            
            # Verify analysis
            assert "tornado_diagram" in sensitivity
            assert "most_sensitive_parameters" in sensitivity
            assert "break_even_analysis" in sensitivity
            
            print(f"\nROI sensitivity analysis:")
            print(f"  Most sensitive parameters: {sensitivity['most_sensitive_parameters']}")
            
            for param, impact in sensitivity["tornado_diagram"][:3]:
                print(f"    {param}: ±{abs(impact['impact_percentage']):.1%} ROI change")
                
            print(f"  Break-even conversion improvement: "
                  f"{sensitivity['break_even_analysis']['conversion_rate_improvement']:.3f}")


# Performance test markers
pytest.mark.performance = pytest.mark.skipif(
    os.getenv("RUN_PERFORMANCE_TESTS", "false").lower() != "true",
    reason="Performance tests disabled by default"
)

# Statistical test markers
pytest.mark.statistical = pytest.mark.skipif(
    os.getenv("RUN_STATISTICAL_TESTS", "true").lower() != "true",
    reason="Statistical tests can be disabled for faster runs"
)


if __name__ == "__main__":
    # Run specific test groups
    pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-k", "TestStatisticalSignificance or TestSampleSizeCalculation",
        "--log-level=INFO"
    ])