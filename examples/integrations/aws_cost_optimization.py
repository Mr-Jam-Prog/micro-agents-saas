"""
AWS Cost Optimization Integration Example
Comprehensive cost optimization across AWS accounts using 200+ cost optimization agents.
"""

import asyncio
import json
import boto3
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Any, Tuple
from decimal import Decimal
import logging
from collections import defaultdict
import statistics
from enum import Enum

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# 1. AWS CLIENT MANAGER & MULTI-ACCOUNT MANAGEMENT
# ============================================================================

class AWSAccount:
    """Represents an AWS account with associated metadata."""
    
    def __init__(self, account_id: str, name: str, role_arn: str, 
                 environment: str, business_unit: str):
        self.account_id = account_id
        self.name = name
        self.role_arn = role_arn
        self.environment = environment
        self.business_unit = business_unit
        self.clients: Dict[str, Any] = {}
        self._assume_role()
    
    def _assume_role(self):
        """Assume cross-account role."""
        try:
            sts_client = boto3.client('sts')
            assumed_role = sts_client.assume_role(
                RoleArn=self.role_arn,
                RoleSessionName=f"CostOptimization-{self.account_id}",
                DurationSeconds=3600
            )
            
            credentials = assumed_role['Credentials']
            
            self.clients = {
                'ce': boto3.client(
                    'ce',
                    aws_access_key_id=credentials['AccessKeyId'],
                    aws_secret_access_key=credentials['SecretAccessKey'],
                    aws_session_token=credentials['SessionToken']
                ),
                'ec2': boto3.client(
                    'ec2',
                    aws_access_key_id=credentials['AccessKeyId'],
                    aws_secret_access_key=credentials['SecretAccessKey'],
                    aws_session_token=credentials['SessionToken']
                ),
                'rds': boto3.client(
                    'rds',
                    aws_access_key_id=credentials['AccessKeyId'],
                    aws_secret_access_key=credentials['SecretAccessKey'],
                    aws_session_token=credentials['SessionToken']
                ),
                's3': boto3.client(
                    's3',
                    aws_access_key_id=credentials['AccessKeyId'],
                    aws_secret_access_key=credentials['SecretAccessKey'],
                    aws_session_token=credentials['SessionToken']
                ),
                'budgets': boto3.client(
                    'budgets',
                    aws_access_key_id=credentials['AccessKeyId'],
                    aws_secret_access_key=credentials['SecretAccessKey'],
                    aws_session_token=credentials['SessionToken']
                ),
                'organizations': boto3.client(
                    'organizations',
                    aws_access_key_id=credentials['AccessKeyId'],
                    aws_secret_access_key=credentials['SecretAccessKey'],
                    aws_session_token=credentials['SessionToken']
                ),
                'compute-optimizer': boto3.client(
                    'compute-optimizer',
                    aws_access_key_id=credentials['AccessKeyId'],
                    aws_secret_access_key=credentials['SecretAccessKey'],
                    aws_session_token=credentials['SessionToken']
                ),
                'resource-groups': boto3.client(
                    'resource-groups',
                    aws_access_key_id=credentials['AccessKeyId'],
                    aws_secret_access_key=credentials['SecretAccessKey'],
                    aws_session_token=credentials['SessionToken']
                )
            }
            logger.info(f"Successfully assumed role for account {self.account_id}")
            
        except Exception as e:
            logger.error(f"Failed to assume role for account {self.account_id}: {e}")
            raise

class AWSMultiAccountManager:
    """Manages multiple AWS accounts for cost optimization."""
    
    def __init__(self, master_account_id: str):
        self.master_account_id = master_account_id
        self.accounts: Dict[str, AWSAccount] = {}
        self._load_accounts()
    
    def _load_accounts(self):
        """Load AWS accounts from configuration or AWS Organizations."""
        # In production, this would load from AWS Organizations or config file
        accounts_config = [
            {
                "account_id": "123456789012",
                "name": "production",
                "role_arn": "arn:aws:iam::123456789012:role/OrganizationAccountAccessRole",
                "environment": "production",
                "business_unit": "ecommerce"
            },
            {
                "account_id": "234567890123",
                "name": "staging",
                "role_arn": "arn:aws:iam::234567890123:role/OrganizationAccountAccessRole",
                "environment": "staging",
                "business_unit": "ecommerce"
            },
            {
                "account_id": "345678901234",
                "name": "development",
                "role_arn": "arn:aws:iam::345678901234:role/OrganizationAccountAccessRole",
                "environment": "development",
                "business_unit": "platform"
            },
            {
                "account_id": "456789012345",
                "name": "analytics",
                "role_arn": "arn:aws:iam::456789012345:role/OrganizationAccountAccessRole",
                "environment": "production",
                "business_unit": "data"
            }
        ]
        
        for config in accounts_config:
            account = AWSAccount(**config)
            self.accounts[account.account_id] = account
        
        logger.info(f"Loaded {len(self.accounts)} AWS accounts")
    
    def get_accounts_by_environment(self, environment: str) -> List[AWSAccount]:
        """Get accounts by environment."""
        return [acc for acc in self.accounts.values() if acc.environment == environment]
    
    def get_accounts_by_business_unit(self, business_unit: str) -> List[AWSAccount]:
        """Get accounts by business unit."""
        return [acc for acc in self.accounts.values() if acc.business_unit == business_unit]
    
    async def execute_across_accounts(self, func, *args, **kwargs) -> Dict[str, Any]:
        """Execute a function across all accounts in parallel."""
        tasks = []
        for account_id, account in self.accounts.items():
            tasks.append(func(account, *args, **kwargs))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        aggregated_results = {}
        for account_id, result in zip(self.accounts.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"Error in account {account_id}: {result}")
                aggregated_results[account_id] = {"error": str(result)}
            else:
                aggregated_results[account_id] = result
        
        return aggregated_results

# ============================================================================
# 2. COST EXPLORER API INTEGRATION
# ============================================================================

class CostExplorerAnalyzer:
    """Analyze AWS costs using Cost Explorer API."""
    
    def __init__(self, aws_account: AWSAccount):
        self.account = aws_account
        self.ce_client = aws_account.clients['ce']
    
    async def get_cost_and_usage(self, start_date: str, end_date: str, 
                                granularity: str = "DAILY") -> Dict[str, Any]:
        """Get cost and usage data from AWS Cost Explorer."""
        try:
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    'Start': start_date,
                    'End': end_date
                },
                Granularity=granularity,
                Metrics=['UnblendedCost', 'UsageQuantity'],
                GroupBy=[
                    {'Type': 'DIMENSION', 'Key': 'SERVICE'},
                    {'Type': 'DIMENSION', 'Key': 'USAGE_TYPE'},
                    {'Type': 'TAG', 'Key': 'Environment'},
                    {'Type': 'TAG', 'Key': 'BusinessUnit'}
                ]
            )
            
            # Process and structure the data
            processed_data = self._process_cost_data(response)
            return processed_data
            
        except Exception as e:
            logger.error(f"Error getting cost data: {e}")
            raise
    
    def _process_cost_data(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Process raw Cost Explorer response."""
        results = {
            'total_cost': Decimal('0'),
            'service_breakdown': {},
            'daily_costs': {},
            'tags_breakdown': {
                'Environment': {},
                'BusinessUnit': {}
            }
        }
        
        for result_by_time in response.get('ResultsByTime', []):
            time_period = result_by_time['TimePeriod']['Start']
            results['daily_costs'][time_period] = Decimal('0')
            
            for group in result_by_time.get('Groups', []):
                # Extract service name
                service = next((dim['Value'] for dim in group['Keys'] 
                              if dim['Type'] == 'DIMENSION' and dim['Key'] == 'SERVICE'), 'Unknown')
                
                # Extract cost
                cost = Decimal(str(group['Metrics']['UnblendedCost']['Amount']))
                
                # Update totals
                results['total_cost'] += cost
                results['daily_costs'][time_period] += cost
                
                # Update service breakdown
                if service not in results['service_breakdown']:
                    results['service_breakdown'][service] = {
                        'total_cost': Decimal('0'),
                        'percentage': 0
                    }
                results['service_breakdown'][service]['total_cost'] += cost
                
                # Extract and process tags
                for dim in group['Keys']:
                    if dim['Type'] == 'TAG':
                        tag_key = dim['Key']
                        tag_value = dim['Value']
                        
                        if tag_key not in results['tags_breakdown']:
                            results['tags_breakdown'][tag_key] = {}
                        
                        if tag_value not in results['tags_breakdown'][tag_key]:
                            results['tags_breakdown'][tag_key][tag_value] = Decimal('0')
                        
                        results['tags_breakdown'][tag_key][tag_value] += cost
        
        # Calculate percentages
        if results['total_cost'] > 0:
            for service in results['service_breakdown']:
                service_cost = results['service_breakdown'][service]['total_cost']
                percentage = (service_cost / results['total_cost']) * 100
                results['service_breakdown'][service]['percentage'] = float(percentage.quantize(Decimal('0.01')))
        
        # Convert Decimal to float for JSON serialization
        results['total_cost'] = float(results['total_cost'])
        results['daily_costs'] = {k: float(v) for k, v in results['daily_costs'].items()}
        results['tags_breakdown'] = {
            k: {vk: float(vv) for vk, vv in v.items()}
            for k, v in results['tags_breakdown'].items()
        }
        
        return results
    
    async def get_cost_forecast(self, forecast_period: int = 30) -> Dict[str, Any]:
        """Get cost forecast for the next period."""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30)
            
            response = self.ce_client.get_cost_forecast(
                TimePeriod={
                    'Start': end_date.strftime('%Y-%m-%d'),
                    'End': (end_date + timedelta(days=forecast_period)).strftime('%Y-%m-%d')
                },
                Metric='UNBLENDED_COST',
                Granularity='MONTHLY'
            )
            
            forecast = {
                'forecast_amount': float(response['Total']['Amount']),
                'currency': response['Total']['Unit'],
                'forecast_period': forecast_period,
                'prediction_interval_lower': float(response.get('ForecastResultsByTime', [{}])[0].get('PredictionIntervalLowerBound', {}).get('Amount', 0)),
                'prediction_interval_upper': float(response.get('ForecastResultsByTime', [{}])[0].get('PredictionIntervalUpperBound', {}).get('Amount', 0))
            }
            
            return forecast
            
        except Exception as e:
            logger.error(f"Error getting cost forecast: {e}")
            raise
    
    async def get_anomalies(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Get cost anomalies using Cost Anomaly Detection."""
        try:
            # Note: Cost Anomaly Detection requires separate setup
            # This is a simplified implementation
            response = self.ce_client.get_anomalies(
                DateInterval={
                    'StartDate': start_date,
                    'EndDate': end_date
                },
                Feedback='YES',
                MaxResults=100
            )
            
            anomalies = []
            for anomaly in response.get('Anomalies', []):
                anomalies.append({
                    'anomaly_id': anomaly['AnomalyId'],
                    'dimension_value': anomaly.get('DimensionValue', 'Unknown'),
                    'anomaly_score': anomaly.get('Impact', {}).get('TotalImpact', 0),
                    'start_date': anomaly['AnomalyStartDate'],
                    'end_date': anomaly.get('AnomalyEndDate', ''),
                    'status': anomaly.get('Status', 'UNKNOWN')
                })
            
            return anomalies
            
        except Exception as e:
            logger.warning(f"Cost Anomaly Detection not configured or error: {e}")
            return []

# ============================================================================
# 3. RESOURCE TAGGING STRATEGY
# ============================================================================

class TaggingStrategy:
    """Implement and enforce AWS resource tagging strategy."""
    
    REQUIRED_TAGS = [
        'Environment',
        'BusinessUnit',
        'Application',
        'Owner',
        'CostCenter'
    ]
    
    OPTIONAL_TAGS = [
        'Version',
        'DataClassification',
        'Compliance',
        'BackupSchedule',
        'ShutdownSchedule'
    ]
    
    def __init__(self, aws_account: AWSAccount):
        self.account = aws_account
        self.ec2_client = aws_account.clients['ec2']
        self.rds_client = aws_account.clients['rds']
        self.s3_client = aws_account.clients['s3']
    
    async def audit_resource_tags(self) -> Dict[str, Any]:
        """Audit resource tagging compliance across services."""
        audit_results = {
            'total_resources': 0,
            'compliant_resources': 0,
            'non_compliant_resources': 0,
            'service_breakdown': {},
            'missing_tags_summary': defaultdict(int)
        }
        
        # Audit EC2 instances
        ec2_audit = await self._audit_ec2_tags()
        audit_results['service_breakdown']['ec2'] = ec2_audit
        
        # Audit RDS instances
        rds_audit = await self._audit_rds_tags()
        audit_results['service_breakdown']['rds'] = rds_audit
        
        # Audit S3 buckets
        s3_audit = await self._audit_s3_tags()
        audit_results['service_breakdown']['s3'] = s3_audit
        
        # Aggregate results
        for service_audit in audit_results['service_breakdown'].values():
            audit_results['total_resources'] += service_audit['total_resources']
            audit_results['compliant_resources'] += service_audit['compliant_resources']
            audit_results['non_compliant_resources'] += service_audit['non_compliant_resources']
            
            for tag, count in service_audit['missing_tags'].items():
                audit_results['missing_tags_summary'][tag] += count
        
        # Calculate compliance percentage
        if audit_results['total_resources'] > 0:
            audit_results['compliance_percentage'] = (
                audit_results['compliant_resources'] / audit_results['total_resources']
            ) * 100
        else:
            audit_results['compliance_percentage'] = 100
        
        return audit_results
    
    async def _audit_ec2_tags(self) -> Dict[str, Any]:
        """Audit EC2 instance tags."""
        try:
            response = self.ec2_client.describe_instances()
            instances = []
            
            for reservation in response['Reservations']:
                instances.extend(reservation['Instances'])
            
            audit = {
                'total_resources': len(instances),
                'compliant_resources': 0,
                'non_compliant_resources': 0,
                'missing_tags': defaultdict(int),
                'instances': []
            }
            
            for instance in instances:
                instance_id = instance['InstanceId']
                tags = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
                
                # Check required tags
                missing_tags = []
                for required_tag in self.REQUIRED_TAGS:
                    if required_tag not in tags:
                        missing_tags.append(required_tag)
                        audit['missing_tags'][required_tag] += 1
                
                instance_audit = {
                    'resource_id': instance_id,
                    'instance_type': instance.get('InstanceType', 'Unknown'),
                    'state': instance.get('State', {}).get('Name', 'Unknown'),
                    'tags': tags,
                    'missing_tags': missing_tags,
                    'is_compliant': len(missing_tags) == 0
                }
                
                audit['instances'].append(instance_audit)
                
                if instance_audit['is_compliant']:
                    audit['compliant_resources'] += 1
                else:
                    audit['non_compliant_resources'] += 1
            
            return audit
            
        except Exception as e:
            logger.error(f"Error auditing EC2 tags: {e}")
            return {
                'total_resources': 0,
                'compliant_resources': 0,
                'non_compliant_resources': 0,
                'missing_tags': {},
                'instances': []
            }
    
    async def _audit_rds_tags(self) -> Dict[str, Any]:
        """Audit RDS instance tags."""
        try:
            response = self.rds_client.describe_db_instances()
            instances = response['DBInstances']
            
            audit = {
                'total_resources': len(instances),
                'compliant_resources': 0,
                'non_compliant_resources': 0,
                'missing_tags': defaultdict(int),
                'instances': []
            }
            
            for instance in instances:
                instance_id = instance['DBInstanceIdentifier']
                
                # Get tags for RDS instance
                tags_response = self.rds_client.list_tags_for_resource(
                    ResourceName=instance['DBInstanceArn']
                )
                tags = {tag['Key']: tag['Value'] for tag in tags_response['TagList']}
                
                # Check required tags
                missing_tags = []
                for required_tag in self.REQUIRED_TAGS:
                    if required_tag not in tags:
                        missing_tags.append(required_tag)
                        audit['missing_tags'][required_tag] += 1
                
                instance_audit = {
                    'resource_id': instance_id,
                    'engine': instance.get('Engine', 'Unknown'),
                    'instance_class': instance.get('DBInstanceClass', 'Unknown'),
                    'tags': tags,
                    'missing_tags': missing_tags,
                    'is_compliant': len(missing_tags) == 0
                }
                
                audit['instances'].append(instance_audit)
                
                if instance_audit['is_compliant']:
                    audit['compliant_resources'] += 1
                else:
                    audit['non_compliant_resources'] += 1
            
            return audit
            
        except Exception as e:
            logger.error(f"Error auditing RDS tags: {e}")
            return {
                'total_resources': 0,
                'compliant_resources': 0,
                'non_compliant_resources': 0,
                'missing_tags': {},
                'instances': []
            }
    
    async def _audit_s3_tags(self) -> Dict[str, Any]:
        """Audit S3 bucket tags."""
        try:
            response = self.s3_client.list_buckets()
            buckets = response['Buckets']
            
            audit = {
                'total_resources': len(buckets),
                'compliant_resources': 0,
                'non_compliant_resources': 0,
                'missing_tags': defaultdict(int),
                'buckets': []
            }
            
            for bucket in buckets:
                bucket_name = bucket['Name']
                
                try:
                    # Get bucket tagging
                    tags_response = self.s3_client.get_bucket_tagging(Bucket=bucket_name)
                    tags = {tag['Key']: tag['Value'] for tag in tags_response['TagSet']}
                except:
                    tags = {}
                
                # Check required tags
                missing_tags = []
                for required_tag in self.REQUIRED_TAGS:
                    if required_tag not in tags:
                        missing_tags.append(required_tag)
                        audit['missing_tags'][required_tag] += 1
                
                bucket_audit = {
                    'resource_id': bucket_name,
                    'creation_date': bucket['CreationDate'].isoformat(),
                    'tags': tags,
                    'missing_tags': missing_tags,
                    'is_compliant': len(missing_tags) == 0
                }
                
                audit['buckets'].append(bucket_audit)
                
                if bucket_audit['is_compliant']:
                    audit['compliant_resources'] += 1
                else:
                    audit['non_compliant_resources'] += 1
            
            return audit
            
        except Exception as e:
            logger.error(f"Error auditing S3 tags: {e}")
            return {
                'total_resources': 0,
                'compliant_resources': 0,
                'non_compliant_resources': 0,
                'missing_tags': {},
                'buckets': []
            }
    
    async def enforce_tags(self, resource_type: str, resource_id: str, 
                          tags: Dict[str, str]) -> bool:
        """Enforce tags on a specific resource."""
        try:
            if resource_type == 'ec2':
                self.ec2_client.create_tags(
                    Resources=[resource_id],
                    Tags=[{'Key': k, 'Value': v} for k, v in tags.items()]
                )
            elif resource_type == 'rds':
                self.rds_client.add_tags_to_resource(
                    ResourceName=resource_id,
                    Tags=[{'Key': k, 'Value': v} for k, v in tags.items()]
                )
            elif resource_type == 's3':
                # For S3, we need to replace all tags
                self.s3_client.put_bucket_tagging(
                    Bucket=resource_id,
                    Tagging={
                        'TagSet': [{'Key': k, 'Value': v} for k, v in tags.items()]
                    }
                )
            
            logger.info(f"Tags enforced on {resource_type}/{resource_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error enforcing tags on {resource_type}/{resource_id}: {e}")
            return False

# ============================================================================
# 4. RESERVED INSTANCE OPTIMIZATION
# ============================================================================

class ReservedInstanceOptimizer:
    """Optimize Reserved Instance purchases and utilization."""
    
    def __init__(self, aws_account: AWSAccount):
        self.account = aws_account
        self.ec2_client = aws_account.clients['ec2']
    
    async def analyze_ri_coverage(self) -> Dict[str, Any]:
        """Analyze Reserved Instance coverage and recommendations."""
        try:
            # Get current Reserved Instances
            ri_response = self.ec2_client.describe_reserved_instances()
            reserved_instances = ri_response['ReservedInstances']
            
            # Get current running instances
            instances_response = self.ec2_client.describe_instances(
                Filters=[
                    {'Name': 'instance-state-name', 'Values': ['running']}
                ]
            )
            
            running_instances = []
            for reservation in instances_response['Reservations']:
                running_instances.extend(reservation['Instances'])
            
            analysis = {
                'reserved_instances': [],
                'running_instances': [],
                'coverage_analysis': {},
                'recommendations': []
            }
            
            # Process Reserved Instances
            for ri in reserved_instances:
                if ri['State'] == 'active':
                    ri_info = {
                        'reserved_instance_id': ri['ReservedInstancesId'],
                        'instance_type': ri['InstanceType'],
                        'availability_zone': ri.get('AvailabilityZone', 'N/A'),
                        'instance_count': ri['InstanceCount'],
                        'state': ri['State'],
                        'remaining_term_months': self._calculate_remaining_term(ri),
                        'offering_class': ri.get('OfferingClass', 'standard'),
                        'scope': ri.get('Scope', 'Region')
                    }
                    analysis['reserved_instances'].append(ri_info)
            
            # Process Running Instances
            instance_type_count = defaultdict(int)
            for instance in running_instances:
                instance_type = instance['InstanceType']
                instance_type_count[instance_type] += 1
                
                instance_info = {
                    'instance_id': instance['InstanceId'],
                    'instance_type': instance_type,
                    'availability_zone': instance.get('Placement', {}).get('AvailabilityZone', 'N/A'),
                    'state': instance.get('State', {}).get('Name', 'running'),
                    'tags': {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
                }
                analysis['running_instances'].append(instance_info)
            
            # Analyze coverage
            for instance_type, count in instance_type_count.items():
                reserved_count = sum(
                    1 for ri in analysis['reserved_instances']
                    if ri['instance_type'] == instance_type and ri['state'] == 'active'
                )
                
                coverage = (reserved_count / count * 100) if count > 0 else 0
                
                analysis['coverage_analysis'][instance_type] = {
                    'running_count': count,
                    'reserved_count': reserved_count,
                    'coverage_percentage': coverage,
                    'uncovered_count': max(0, count - reserved_count)
                }
                
                # Generate recommendations
                if coverage < 80:
                    analysis['recommendations'].append({
                        'priority': 'HIGH',
                        'action': f'Purchase Reserved Instances for {instance_type}',
                        'details': f'Only {coverage:.1f}% covered ({reserved_count}/{count})',
                        'estimated_savings': self._estimate_ri_savings(instance_type, max(0, count - reserved_count))
                    })
                elif coverage > 120:
                    analysis['recommendations'].append({
                        'priority': 'MEDIUM',
                        'action': f'Consider modifying or selling excess Reserved Instances for {instance_type}',
                        'details': f'{coverage:.1f}% coverage ({reserved_count}/{count})'
                    })
            
            # Check for expiring RIs
            expiring_ris = [
                ri for ri in analysis['reserved_instances']
                if ri['remaining_term_months'] < 3
            ]
            
            if expiring_ris:
                analysis['recommendations'].append({
                    'priority': 'HIGH',
                    'action': 'Renew expiring Reserved Instances',
                    'details': f'{len(expiring_ris)} RIs expiring within 3 months',
                    'ris': [ri['reserved_instance_id'] for ri in expiring_ris]
                })
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing RI coverage: {e}")
            raise
    
    def _calculate_remaining_term(self, ri: Dict[str, Any]) -> int:
        """Calculate remaining term in months for a Reserved Instance."""
        if ri['State'] != 'active':
            return 0
        
        end_time = ri['End']
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        
        remaining_days = (end_time - datetime.now()).days
        return max(0, remaining_days // 30)
    
    def _estimate_ri_savings(self, instance_type: str, count: int) -> float:
        """Estimate savings from purchasing Reserved Instances."""
        # Simplified estimation - in production, use AWS Pricing API
        savings_rates = {
            't3.micro': 30,
            't3.small': 60,
            't3.medium': 120,
            'm5.large': 180,
            'm5.xlarge': 360,
            'c5.large': 150,
            'c5.xlarge': 300
        }
        
        monthly_savings = savings_rates.get(instance_type, 100) * count
        annual_savings = monthly_savings * 12
        
        return annual_savings
    
    async def get_ri_recommendations(self) -> List[Dict[str, Any]]:
        """Get Reserved Instance purchase recommendations."""
        try:
            # Use AWS Compute Optimizer for RI recommendations
            compute_optimizer = self.account.clients['compute-optimizer']
            
            response = compute_optimizer.get_ec2_instance_recommendations(
                recommendationPreferences={
                    'cpuVendorArchitectures': ['AWS_ARM64', 'CURRENT'],
                    'savingsEstimationMode': 'AFTER_DISCOUNTS'
                }
            )
            
            recommendations = []
            for rec in response.get('instanceRecommendations', []):
                if rec.get('recommendationOptions'):
                    for option in rec['recommendationOptions']:
                        if option.get('instanceType') != rec.get('currentInstanceType'):
                            savings = option.get('savingsOpportunity', {}).get('estimatedMonthlySavings', {})
                            
                            recommendations.append({
                                'instance_id': rec.get('instanceArn', '').split('/')[-1],
                                'current_instance_type': rec.get('currentInstanceType'),
                                'recommended_instance_type': option.get('instanceType'),
                                'estimated_monthly_savings': float(savings.get('value', 0)),
                                'savings_currency': savings.get('currency', 'USD'),
                                'performance_risk': option.get('performanceRisk', 'Low'),
                                'migration_effort': option.get('migrationEffort', 'VeryLow')
                            })
            
            return recommendations
            
        except Exception as e:
            logger.warning(f"Compute Optimizer not available or error: {e}")
            return []

# ============================================================================
# 5. SAVINGS PLANS RECOMMENDATIONS
# ============================================================================

class SavingsPlansAnalyzer:
    """Analyze and optimize AWS Savings Plans."""
    
    def __init__(self, aws_account: AWSAccount):
        self.account = aws_account
        self.ce_client = aws_account.clients['ce']
    
    async def analyze_savings_plans(self) -> Dict[str, Any]:
        """Analyze current Savings Plans and get recommendations."""
        try:
            # Get Savings Plans coverage
            response = self.ce_client.get_savings_plans_utilization(
                TimePeriod={
                    'Start': (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
                    'End': datetime.now().strftime('%Y-%m-%d')
                },
                Granularity='DAILY'
            )
            
            analysis = {
                'total_savings_plans': [],
                'utilization_metrics': {},
                'recommendations': []
            }
            
            # Process Savings Plans
            for sp in response.get('SavingsPlansUtilizationsByTime', []):
                for sp_detail in sp.get('Utilization', {}).get('SavingsPlans', []):
                    sp_info = {
                        'savings_plan_id': sp_detail.get('SavingsPlanArn', '').split('/')[-1],
                        'type': sp_detail.get('SavingsPlanType', 'Unknown'),
                        'payment_option': sp_detail.get('PaymentOption', 'Unknown'),
                        'commitment': float(sp_detail.get('Commitment', '0')),
                        'savings': float(sp_detail.get('Savings', {}).get('NetSavings', '0')),
                        'utilization_percentage': float(sp_detail.get('UtilizationPercentage', '0')),
                        'date': sp['TimePeriod']['Start']
                    }
                    analysis['total_savings_plans'].append(sp_info)
            
            # Calculate average utilization
            if analysis['total_savings_plans']:
                utilizations = [sp['utilization_percentage'] for sp in analysis['total_savings_plans']]
                analysis['utilization_metrics'] = {
                    'average_utilization': statistics.mean(utilizations),
                    'min_utilization': min(utilizations),
                    'max_utilization': max(utilizations)
                }
                
                # Generate recommendations based on utilization
                avg_util = analysis['utilization_metrics']['average_utilization']
                if avg_util < 70:
                    analysis['recommendations'].append({
                        'priority': 'HIGH',
                        'action': 'Optimize Savings Plans utilization',
                        'details': f'Average utilization is only {avg_util:.1f}%',
                        'suggestion': 'Consider downsizing or changing commitment'
                    })
                elif avg_util > 95:
                    analysis['recommendations'].append({
                        'priority': 'MEDIUM',
                        'action': 'Consider purchasing additional Savings Plans',
                        'details': f'High utilization at {avg_util:.1f}%',
                        'suggestion': 'Monitor for potential coverage gaps'
                    })
            
            # Get Savings Plans recommendations
            rec_response = self.ce_client.get_savings_plans_purchase_recommendation(
                LookbackPeriodInDays=30,
                TermInYears=1,
                PaymentOption='NO_UPFRONT',
                SavingsPlansType='COMPUTE_SP'
            )
            
            for rec in rec_response.get('SavingsPlansPurchaseRecommendation', []):
                analysis['recommendations'].append({
                    'priority': 'HIGH',
                    'action': 'Purchase new Savings Plan',
                    'details': f"{rec.get('SavingsPlansType', 'Unknown')} - {rec.get('PaymentOption', 'Unknown')}",
                    'estimated_monthly_cost': float(rec.get('EstimatedMonthlySavingsAmount', '0')),
                    'estimated_savings_percentage': float(rec.get('EstimatedSavingsPercentage', '0'))
                })
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing Savings Plans: {e}")
            raise
    
    async def calculate_savings_plan_benefits(self, commitment_amount: float, 
                                             plan_type: str = 'COMPUTE_SP') -> Dict[str, Any]:
        """Calculate potential benefits of a Savings Plan."""
        try:
            # Get cost without Savings Plan
            cost_response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    'Start': (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
                    'End': datetime.now().strftime('%Y-%m-%d')
                },
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
                Filter={
                    'Dimensions': {
                        'Key': 'SERVICE',
                        'Values': ['AmazonEC2', 'AWSLambda', 'AmazonECS']  # Compute services
                    }
                }
            )
            
            monthly_cost = Decimal('0')
            for result in cost_response.get('ResultsByTime', []):
                monthly_cost += Decimal(str(result['Total']['UnblendedCost']['Amount']))
            
            # Simplified calculation - in production, use more accurate formulas
            monthly_commitment = commitment_amount / 12
            estimated_savings = float(monthly_cost) - monthly_commitment
            
            if estimated_savings > 0:
                savings_percentage = (estimated_savings / float(monthly_cost)) * 100
            else:
                savings_percentage = 0
            
            return {
                'plan_type': plan_type,
                'commitment_amount': commitment_amount,
                'monthly_commitment': monthly_commitment,
                'current_monthly_cost': float(monthly_cost),
                'estimated_monthly_savings': max(0, estimated_savings),
                'estimated_savings_percentage': savings_percentage,
                'break_even_months': commitment_amount / max(estimated_savings, 0.01) if estimated_savings > 0 else None
            }
            
        except Exception as e:
            logger.error(f"Error calculating Savings Plan benefits: {e}")
            raise

# ============================================================================
# 6. IDLE RESOURCE IDENTIFICATION
# ============================================================================

class IdleResourceDetector:
    """Detect idle or underutilized AWS resources."""
    
    def __init__(self, aws_account: AWSAccount):
        self.account = aws_account
        self.cloudwatch = boto3.client('cloudwatch')
        self.ec2_client = aws_account.clients['ec2']
    
    async def detect_idle_ec2_instances(self, days_to_analyze: int = 7) -> List[Dict[str, Any]]:
        """Detect idle EC2 instances based on CloudWatch metrics."""
        try:
            # Get all running instances
            instances_response = self.ec2_client.describe_instances(
                Filters=[
                    {'Name': 'instance-state-name', 'Values': ['running']}
                ]
            )
            
            idle_instances = []
            
            for reservation in instances_response['Reservations']:
                for instance in reservation['Instances']:
                    instance_id = instance['InstanceId']
                    instance_type = instance['InstanceType']
                    
                    # Check CPU utilization
                    cpu_stats = await self._get_cpu_utilization(instance_id, days_to_analyze)
                    
                    # Check network activity
                    network_stats = await self._get_network_activity(instance_id, days_to_analyze)
                    
                    # Determine if instance is idle
                    is_idle = self._is_instance_idle(cpu_stats, network_stats)
                    
                    if is_idle:
                        estimated_monthly_cost = self._estimate_instance_cost(instance_type)
                        
                        idle_instances.append({
                            'instance_id': instance_id,
                            'instance_type': instance_type,
                            'launch_time': instance.get('LaunchTime', '').isoformat(),
                            'tags': {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])},
                            'cpu_utilization': cpu_stats,
                            'network_activity': network_stats,
                            'estimated_monthly_cost': estimated_monthly_cost,
                            'recommended_action': 'Stop or terminate if not needed',
                            'potential_savings': estimated_monthly_cost
                        })
            
            return idle_instances
            
        except Exception as e:
            logger.error(f"Error detecting idle EC2 instances: {e}")
            raise
    
    async def _get_cpu_utilization(self, instance_id: str, days: int) -> Dict[str, float]:
        """Get CPU utilization statistics from CloudWatch."""
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)
            
            response = self.cloudwatch.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='CPUUtilization',
                Dimensions=[
                    {'Name': 'InstanceId', 'Value': instance_id}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour
                Statistics=['Average', 'Maximum']
            )
            
            datapoints = response.get('Datapoints', [])
            if datapoints:
                avg_values = [dp['Average'] for dp in datapoints]
                max_values = [dp['Maximum'] for dp in datapoints if 'Maximum' in dp]
                
                return {
                    'average': statistics.mean(avg_values) if avg_values else 0,
                    'maximum': max(max_values) if max_values else 0,
                    'percentile_95': statistics.quantiles(avg_values, n=20)[18] if len(avg_values) >= 20 else 0
                }
            else:
                return {'average': 0, 'maximum': 0, 'percentile_95': 0}
                
        except Exception as e:
            logger.warning(f"Error getting CPU stats for {instance_id}: {e}")
            return {'average': 0, 'maximum': 0, 'percentile_95': 0}
    
    async def _get_network_activity(self, instance_id: str, days: int) -> Dict[str, float]:
        """Get network activity statistics from CloudWatch."""
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)
            
            # Get NetworkIn
            response_in = self.cloudwatch.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='NetworkIn',
                Dimensions=[
                    {'Name': 'InstanceId', 'Value': instance_id}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Sum']
            )
            
            # Get NetworkOut
            response_out = self.cloudwatch.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='NetworkOut',
                Dimensions=[
                    {'Name': 'InstanceId', 'Value': instance_id}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Sum']
            )
            
            datapoints_in = response_in.get('Datapoints', [])
            datapoints_out = response_out.get('Datapoints', [])
            
            if datapoints_in or datapoints_out:
                values_in = [dp['Sum'] for dp in datapoints_in]
                values_out = [dp['Sum'] for dp in datapoints_out]
                
                return {
                    'average_in': statistics.mean(values_in) if values_in else 0,
                    'average_out': statistics.mean(values_out) if values_out else 0,
                    'total_in': sum(values_in),
                    'total_out': sum(values_out)
                }
            else:
                return {'average_in': 0, 'average_out': 0, 'total_in': 0, 'total_out': 0}
                
        except Exception as e:
            logger.warning(f"Error getting network stats for {instance_id}: {e}")
            return {'average_in': 0, 'average_out': 0, 'total_in': 0, 'total_out': 0}
    
    def _is_instance_idle(self, cpu_stats: Dict[str, float], 
                         network_stats: Dict[str, float]) -> bool:
        """Determine if an instance is idle based on metrics."""
        # Criteria for idle instance:
        # 1. Average CPU utilization < 5%
        # 2. Maximum CPU utilization < 10%
        # 3. Low network activity (< 1 MB per hour average)
        
        is_cpu_idle = cpu_stats['average'] < 5 and cpu_stats['maximum'] < 10
        is_network_idle = (network_stats['average_in'] < 1024 and  # 1 KB/s
                          network_stats['average_out'] < 1024)
        
        return is_cpu_idle and is_network_idle
    
    def _estimate_instance_cost(self, instance_type: str) -> float:
        """Estimate monthly cost for an instance type."""
        # Simplified pricing - in production, use AWS Pricing API
        pricing = {
            't3.micro': 8.50,
            't3.small': 17.00,
            't3.medium': 34.00,
            'm5.large': 96.00,
            'm5.xlarge': 192.00,
            'c5.large': 85.00,
            'c5.xlarge': 170.00,
            'r5.large': 126.00,
            'r5.xlarge': 252.00
        }
        
        return pricing.get(instance_type, 100.00)  # Default $100/month
    
    async def detect_unattached_ebs_volumes(self) -> List[Dict[str, Any]]:
        """Detect unattached EBS volumes."""
        try:
            response = self.ec2_client.describe_volumes(
                Filters=[
                    {'Name': 'status', 'Values': ['available']}
                ]
            )
            
            unattached_volumes = []
            
            for volume in response['Volumes']:
                volume_id = volume['VolumeId']
                size_gb = volume['Size']
                volume_type = volume['VolumeType']
                create_time = volume['CreateTime'].isoformat()
                
                # Estimate monthly cost
                cost_per_gb = {
                    'gp2': 0.10,
                    'gp3': 0.08,
                    'io1': 0.125,
                    'io2': 0.125,
                    'st1': 0.045,
                    'sc1': 0.025
                }.get(volume_type, 0.10)
                
                monthly_cost = size_gb * cost_per_gb
                
                unattached_volumes.append({
                    'volume_id': volume_id,
                    'size_gb': size_gb,
                    'volume_type': volume_type,
                    'create_time': create_time,
                    'az': volume['AvailabilityZone'],
                    'encrypted': volume.get('Encrypted', False),
                    'estimated_monthly_cost': monthly_cost,
                    'recommended_action': 'Delete if not needed',
                    'potential_savings': monthly_cost
                })
            
            return unattached_volumes
            
        except Exception as e:
            logger.error(f"Error detecting unattached EBS volumes: {e}")
            raise
    
    async def detect_unused_elastic_ips(self) -> List[Dict[str, Any]]:
        """Detect unused Elastic IP addresses."""
        try:
            response = self.ec2_client.describe_addresses()
            
            unused_eips = []
            
            for address in response['Addresses']:
                if 'InstanceId' not in address and 'NetworkInterfaceId' not in address:
                    eip_info = {
                        'allocation_id': address['AllocationId'],
                        'public_ip': address.get('PublicIp', ''),
                        'association_id': address.get('AssociationId', ''),
                        'estimated_monthly_cost': 3.60,  # $3.60/month for unattached EIP
                        'recommended_action': 'Release if not needed',
                        'potential_savings': 3.60
                    }
                    unused_eips.append(eip_info)
            
            return unused_eips
            
        except Exception as e:
            logger.error(f"Error detecting unused Elastic IPs: {e}")
            raise

# ============================================================================
# 7. STORAGE OPTIMIZATION
# ============================================================================

class StorageOptimizer:
    """Optimize AWS storage costs."""
    
    def __init__(self, aws_account: AWSAccount):
        self.account = aws_account
        self.s3_client = aws_account.clients['s3']
        self.efs_client = boto3.client('efs')
    
    async def analyze_s3_storage(self) -> Dict[str, Any]:
        """Analyze S3 storage for optimization opportunities."""
        try:
            response = self.s3_client.list_buckets()
            buckets = response['Buckets']
            
            analysis = {
                'total_buckets': len(buckets),
                'bucket_analysis': [],
                'total_estimated_cost': 0,
                'optimization_opportunities': []
            }
            
            for bucket in buckets:
                bucket_name = bucket['Name']
                
                try:
                    # Get bucket metrics (simplified - in production, use S3 Inventory or CloudWatch)
                    bucket_analysis = await self._analyze_single_bucket(bucket_name)
                    analysis['bucket_analysis'].append(bucket_analysis)
                    
                    analysis['total_estimated_cost'] += bucket_analysis.get('estimated_monthly_cost', 0)
                    
                    # Check for optimization opportunities
                    if bucket_analysis.get('optimization_opportunities'):
                        analysis['optimization_opportunities'].extend(
                            bucket_analysis['optimization_opportunities']
                        )
                        
                except Exception as e:
                    logger.warning(f"Error analyzing bucket {bucket_name}: {e}")
                    continue
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing S3 storage: {e}")
            raise
    
    async def _analyze_single_bucket(self, bucket_name: str) -> Dict[str, Any]:
        """Analyze a single S3 bucket for optimization."""
        # Note: In production, use S3 Inventory or detailed CloudWatch metrics
        # This is a simplified implementation
        
        bucket_analysis = {
            'bucket_name': bucket_name,
            'total_objects': 0,
            'total_size_gb': 0,
            'storage_classes': defaultdict(float),
            'estimated_monthly_cost': 0,
            'optimization_opportunities': []
        }
        
        try:
            # List objects with pagination
            paginator = self.s3_client.get_paginator('list_objects_v2')
            total_size = 0
            total_objects = 0
            
            for page in paginator.paginate(Bucket=bucket_name):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        total_size += obj['Size']
                        total_objects += 1
                        
                        # Track storage class
                        storage_class = obj.get('StorageClass', 'STANDARD')
                        bucket_analysis['storage_classes'][storage_class] += obj['Size']
            
            # Convert size to GB
            total_size_gb = total_size / (1024 ** 3)
            
            bucket_analysis['total_objects'] = total_objects
            bucket_analysis['total_size_gb'] = total_size_gb
            
            # Estimate cost
            cost_estimates = self._estimate_s3_cost(total_size_gb, bucket_analysis['storage_classes'])
            bucket_analysis['estimated_monthly_cost'] = cost_estimates['monthly_cost']
            bucket_analysis['cost_breakdown'] = cost_estimates['breakdown']
            
            # Check for optimization opportunities
            if total_size_gb > 100:  # If bucket has more than 100GB
                # Check if using STANDARD for infrequently accessed data
                standard_size = bucket_analysis['storage_classes'].get('STANDARD', 0) / (1024 ** 3)
                if standard_size > 50:
                    bucket_analysis['optimization_opportunities'].append({
                        'type': 'STORAGE_CLASS_OPTIMIZATION',
                        'description': f'Consider moving {standard_size:.1f}GB to STANDARD_IA or INTELLIGENT_TIERING',
                        'estimated_savings': standard_size * 0.02 * 30,  # Rough estimate
                        'priority': 'MEDIUM'
                    })
            
            # Check lifecycle policies
            try:
                lifecycle = self.s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
                bucket_analysis['has_lifecycle_policies'] = True
            except:
                bucket_analysis['has_lifecycle_policies'] = False
                bucket_analysis['optimization_opportunities'].append({
                    'type': 'LIFECYCLE_POLICY',
                    'description': 'No lifecycle policies configured',
                    'recommendation': 'Configure lifecycle policies for automatic tiering',
                    'priority': 'HIGH'
                })
            
            return bucket_analysis
            
        except Exception as e:
            logger.warning(f"Error analyzing bucket {bucket_name}: {e}")
            return bucket_analysis
    
    def _estimate_s3_cost(self, total_size_gb: float, 
                         storage_classes: Dict[str, float]) -> Dict[str, Any]:
        """Estimate S3 storage costs."""
        # Pricing per GB per month (simplified)
        pricing = {
            'STANDARD': 0.023,
            'STANDARD_IA': 0.0125,
            'ONEZONE_IA': 0.01,
            'GLACIER': 0.004,
            'DEEP_ARCHIVE': 0.00099,
            'INTELLIGENT_TIERING': 0.023  # Same as STANDARD for first tier
        }
        
        monthly_cost = 0
        breakdown = {}
        
        for storage_class, size_bytes in storage_classes.items():
            size_gb = size_bytes / (1024 ** 3)
            price_per_gb = pricing.get(storage_class, 0.023)
            class_cost = size_gb * price_per_gb
            
            monthly_cost += class_cost
            breakdown[storage_class] = {
                'size_gb': size_gb,
                'cost': class_cost
            }
        
        return {
            'monthly_cost': monthly_cost,
            'breakdown': breakdown
        }
    
    async def optimize_ebs_storage(self) -> List[Dict[str, Any]]:
        """Optimize EBS storage by recommending volume type changes."""
        try:
            response = self.account.clients['ec2'].describe_volumes()
            
            optimization_opportunities = []
            
            for volume in response['Volumes']:
                volume_id = volume['VolumeId']
                current_type = volume['VolumeType']
                size_gb = volume['Size']
                iops = volume.get('Iops', 0)
                throughput = volume.get('Throughput', 0)
                
                # Check if gp2 can be migrated to gp3
                if current_type == 'gp2':
                    recommended_type = 'gp3'
                    
                    # Calculate potential savings
                    gp2_cost = size_gb * 0.10  # $0.10/GB-month for gp2
                    gp3_cost = size_gb * 0.08  # $0.08/GB-month for gp3
                    
                    if gp2_cost > gp3_cost:
                        monthly_savings = gp2_cost - gp3_cost
                        
                        optimization_opportunities.append({
                            'volume_id': volume_id,
                            'current_type': current_type,
                            'recommended_type': recommended_type,
                            'size_gb': size_gb,
                            'current_monthly_cost': gp2_cost,
                            'recommended_monthly_cost': gp3_cost,
                            'estimated_monthly_savings': monthly_savings,
                            'action': 'Migrate from gp2 to gp3',
                            'priority': 'HIGH'
                        })
                
                # Check for over-provisioned IOPS
                elif current_type == 'io1' or current_type == 'io2':
                    # Check if IOPS are significantly higher than needed
                    # (simplified - in production, analyze actual usage)
                    if iops > size_gb * 50:  # More than 50 IOPS/GB
                        recommended_iops = max(100, size_gb * 30)  # Recommend 30 IOPS/GB
                        
                        current_cost = size_gb * 0.125 + (iops * 0.065)  # Simplified pricing
                        recommended_cost = size_gb * 0.125 + (recommended_iops * 0.065)
                        
                        monthly_savings = current_cost - recommended_cost
                        
                        if monthly_savings > 5:  # Only if savings > $5/month
                            optimization_opportunities.append({
                                'volume_id': volume_id,
                                'current_type': current_type,
                                'current_iops': iops,
                                'recommended_iops': recommended_iops,
                                'estimated_monthly_savings': monthly_savings,
                                'action': 'Reduce provisioned IOPS',
                                'priority': 'MEDIUM'
                            })
            
            return optimization_opportunities
            
        except Exception as e:
            logger.error(f"Error optimizing EBS storage: {e}")
            raise

# ============================================================================
# 8. NETWORK COST OPTIMIZATION
# ============================================================================

class NetworkCostOptimizer:
    """Optimize AWS network costs."""
    
    def __init__(self, aws_account: AWSAccount):
        self.account = aws_account
        self.ec2_client = aws_account.clients['ec2']
        self.vpc_client = boto3.client('ec2')  # Using default region
    
    async def analyze_data_transfer_costs(self) -> Dict[str, Any]:
        """Analyze data transfer costs and optimization opportunities."""
        try:
            # This is a simplified analysis
            # In production, use Cost Explorer with detailed filters
            
            analysis = {
                'estimated_monthly_transfer_cost': 0,
                'breakdown_by_direction': {
                    'inbound': 0,
                    'outbound': 0,
                    'intra_region': 0,
                    'inter_region': 0,
                    'internet_outbound': 0
                },
                'optimization_opportunities': []
            }
            
            # Get NAT Gateway usage (major source of data transfer costs)
            nat_gateways = self.ec2_client.describe_nat_gateways()
            
            for nat in nat_gateways.get('NatGateways', []):
                nat_id = nat['NatGatewayId']
                state = nat['State']
                
                if state == 'available':
                    # Estimate costs (simplified - in production, use CloudWatch metrics)
                    analysis['breakdown_by_direction']['internet_outbound'] += 100  # Example $100/month
                    
                    # Check for optimization opportunity
                    analysis['optimization_opportunities'].append({
                        'resource_type': 'NAT_GATEWAY',
                        'resource_id': nat_id,
                        'estimated_monthly_cost': 100,  # $100/month for NAT Gateway + data transfer
                        'recommendation': 'Consider using VPC Endpoints or private connectivity',
                        'estimated_savings': 50,  # 50% potential savings
                        'priority': 'MEDIUM'
                    })
            
            # Estimate total
            analysis['estimated_monthly_transfer_cost'] = sum(
                analysis['breakdown_by_direction'].values()
            )
            
            # Check for Direct Connect opportunities
            if analysis['estimated_monthly_transfer_cost'] > 1000:
                analysis['optimization_opportunities'].append({
                    'resource_type': 'DATA_TRANSFER',
                    'description': 'High data transfer costs',
                    'recommendation': 'Consider AWS Direct Connect for consistent high-volume transfers',
                    'estimated_savings': analysis['estimated_monthly_transfer_cost'] * 0.3,  # 30% savings
                    'priority': 'HIGH'
                })
            
            # Check for CDN opportunities
            analysis['optimization_opportunities'].append({
                'resource_type': 'CDN',
                'description': 'Use CloudFront for static content',
                'recommendation': 'Configure CloudFront distribution for static assets',
                'estimated_savings': analysis['breakdown_by_direction']['internet_outbound'] * 0.5,
                'priority': 'LOW'
            })
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing data transfer costs: {e}")
            raise
    
    async def optimize_vpc_endpoints(self) -> List[Dict[str, Any]]:
        """Recommend VPC Endpoint configurations to reduce NAT Gateway costs."""
        try:
            # Get current VPC Endpoints
            endpoints_response = self.ec2_client.describe_vpc_endpoints()
            current_endpoints = endpoints_response.get('VpcEndpoints', [])
            
            # Services that commonly benefit from VPC Endpoints
            recommended_services = [
                's3',
                'dynamodb',
                'ec2',
                'ecr.api',
                'ecr.dkr',
                'logs',
                'monitoring'
            ]
            
            recommendations = []
            
            for service in recommended_services:
                # Check if endpoint already exists
                endpoint_exists = any(
                    ep.get('ServiceName', '').endswith(f'.{service}.')
                    for ep in current_endpoints
                )
                
                if not endpoint_exists:
                    recommendations.append({
                        'service': service,
                        'type': 'Interface' if service in ['ecr.api', 'ecr.dkr', 'logs', 'monitoring'] else 'Gateway',
                        'estimated_monthly_savings': 50,  # Rough estimate
                        'recommendation': f'Create VPC Endpoint for {service}',
                        'priority': 'HIGH' if service in ['s3', 'dynamodb'] else 'MEDIUM'
                    })
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Error optimizing VPC endpoints: {e}")
            raise

# ============================================================================
# 9. COST ALLOCATION TAGS
# ============================================================================

class CostAllocationManager:
    """Manage cost allocation tags for accurate cost attribution."""
    
    def __init__(self, aws_account: AWSAccount):
        self.account = aws_account
        self.org_client = aws_account.clients['organizations']
    
    async def enable_cost_allocation_tags(self) -> Dict[str, Any]:
        """Enable and configure cost allocation tags."""
        try:
            result = {
                'enabled_tags': [],
                'pending_tags': [],
                'recommendations': []
            }
            
            # Get current cost allocation tags
            # Note: This requires Organizations to be set up
            try:
                tags_response = self.org_client.list_cost_allocation_tags()
                
                for tag in tags_response.get('CostAllocationTags', []):
                    tag_info = {
                        'tag_key': tag.get('TagKey', ''),
                        'status': tag.get('Status', ''),
                        'type': tag.get('Type', '')
                    }
                    
                    if tag.get('Status') == 'Active':
                        result['enabled_tags'].append(tag_info)
                    else:
                        result['pending_tags'].append(tag_info)
                        
            except Exception as e:
                result['recommendations'].append({
                    'action': 'Set up AWS Organizations',
                    'details': 'Cost allocation tags require AWS Organizations',
                    'priority': 'HIGH'
                })
                return result
            
            # Check for recommended tags that should be enabled
            recommended_tags = [
                'Environment',
                'BusinessUnit',
                'Application',
                'Team',
                'CostCenter'
            ]
            
            for tag_key in recommended_tags:
                if not any(t['tag_key'] == tag_key for t in result['enabled_tags']):
                    result['recommendations'].append({
                        'action': f'Enable cost allocation tag: {tag_key}',
                        'details': 'Required for accurate cost attribution',
                        'priority': 'HIGH'
                    })
            
            return result
            
        except Exception as e:
            logger.error(f"Error managing cost allocation tags: {e}")
            raise
    
    async def create_tag_policies(self) -> List[Dict[str, Any]]:
        """Create tag policies for consistent tagging."""
        try:
            policies = []
            
            # Define tag policies for different resource types
            tag_policy_definitions = [
                {
                    'name': 'ec2-tag-policy',
                    'description': 'Tag policy for EC2 instances',
                    'resource_types': ['ec2:instance'],
                    'tags': {
                        'Environment': {
                            'tag_key': 'Environment',
                            'tag_value': ['production', 'staging', 'development'],
                            'enforce': True
                        },
                        'BusinessUnit': {
                            'tag_key': 'BusinessUnit',
                            'enforce': True
                        }
                    }
                },
                {
                    'name': 's3-tag-policy',
                    'description': 'Tag policy for S3 buckets',
                    'resource_types': ['s3:bucket'],
                    'tags': {
                        'DataClassification': {
                            'tag_key': 'DataClassification',
                            'tag_value': ['public', 'internal', 'confidential', 'restricted'],
                            'enforce': True
                        }
                    }
                }
            ]
            
            for policy_def in tag_policy_definitions:
                policies.append({
                    'policy_name': policy_def['name'],
                    'description': policy_def['description'],
                    'resource_types': policy_def['resource_types'],
                    'tags': policy_def['tags'],
                    'status': 'RECOMMENDED'
                })
            
            return policies
            
        except Exception as e:
            logger.error(f"Error creating tag policies: {e}")
            raise

# ============================================================================
# 10. BUDGET ENFORCEMENT
# ============================================================================

class BudgetEnforcer:
    """Enforce budgets and implement cost controls."""
    
    def __init__(self, aws_account: AWSAccount):
        self.account = aws_account
        self.budgets_client = aws_account.clients['budgets']
    
    async def analyze_budgets(self) -> Dict[str, Any]:
        """Analyze current budget configurations."""
        try:
            response = self.budgets_client.describe_budgets(AccountId=self.account.account_id)
            
            analysis = {
                'total_budgets': len(response.get('Budgets', [])),
                'budgets': [],
                'recommendations': []
            }
            
            for budget in response.get('Budgets', []):
                budget_info = {
                    'budget_name': budget.get('BudgetName', ''),
                    'budget_type': budget.get('BudgetType', ''),
                    'time_unit': budget.get('TimeUnit', ''),
                    'budget_limit': budget.get('BudgetLimit', {}).get('Amount', '0'),
                    'actual_spend': budget.get('CalculatedSpend', {}).get('ActualSpend', {}).get('Amount', '0'),
                    'forecasted_spend': budget.get('CalculatedSpend', {}).get('ForecastedSpend', {}).get('Amount', '0'),
                    'threshold': budget.get('CostTypes', {}),
                    'notifications': []
                }
                
                # Get notifications
                try:
                    notifications_response = self.budgets_client.describe_notifications_for_budget(
                        AccountId=self.account.account_id,
                        BudgetName=budget_info['budget_name']
                    )
                    budget_info['notifications'] = notifications_response.get('Notifications', [])
                except:
                    pass
                
                analysis['budgets'].append(budget_info)
                
                # Check if budget has notifications
                if not budget_info['notifications']:
                    analysis['recommendations'].append({
                        'budget': budget_info['budget_name'],
                        'action': 'Add notifications to budget',
                        'details': 'No notifications configured',
                        'priority': 'HIGH'
                    })
            
            # Check if critical budgets are missing
            required_budgets = ['Monthly Total', 'Production Services', 'Development Environment']
            for required in required_budgets:
                if not any(b['budget_name'] == required for b in analysis['budgets']):
                    analysis['recommendations'].append({
                        'budget': required,
                        'action': 'Create budget',
                        'details': f'Missing required budget: {required}',
                        'priority': 'HIGH'
                    })
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing budgets: {e}")
            raise
    
    async def create_budget(self, budget_name: str, amount: float, 
                           notifications: List[Dict[str, Any]]) -> bool:
        """Create a new budget with notifications."""
        try:
            self.budgets_client.create_budget(
                AccountId=self.account.account_id,
                Budget={
                    'BudgetName': budget_name,
                    'BudgetLimit': {
                        'Amount': str(amount),
                        'Unit': 'USD'
                    },
                    'CostTypes': {
                        'IncludeTax': True,
                        'IncludeSubscription': True,
                        'UseBlended': False,
                        'IncludeRefund': False,
                        'IncludeCredit': False,
                        'IncludeUpfront': True,
                        'IncludeRecurring': True,
                        'IncludeOtherSubscription': True,
                        'IncludeSupport': True,
                        'IncludeDiscount': True,
                        'UseAmortized': False
                    },
                    'TimeUnit': 'MONTHLY',
                    'BudgetType': 'COST'
                },
                NotificationsWithSubscribers=notifications
            )
            
            logger.info(f"Budget created: {budget_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating budget: {e}")
            return False
    
    async def enforce_cost_controls(self, threshold_percentage: float = 90) -> Dict[str, Any]:
        """Enforce cost controls when thresholds are exceeded."""
        try:
            controls = {
                'actions_taken': [],
                'recommendations': []
            }
            
            # Get current month spend
            ce_analyzer = CostExplorerAnalyzer(self.account)
            today = datetime.now()
            first_of_month = today.replace(day=1)
            
            cost_data = await ce_analyzer.get_cost_and_usage(
                start_date=first_of_month.strftime('%Y-%m-%d'),
                end_date=today.strftime('%Y-%m-%d'),
                granularity='MONTHLY'
            )
            
            current_spend = cost_data.get('total_cost', 0)
            
            # Get budgets
            budget_analysis = await self.analyze_budgets()
            
            for budget in budget_analysis['budgets']:
                budget_limit = float(budget.get('budget_limit', 0))
                budget_name = budget.get('budget_name', '')
                
                if budget_limit > 0:
                    percentage_used = (current_spend / budget_limit) * 100
                    
                    if percentage_used > threshold_percentage:
                        controls['actions_taken'].append({
                            'budget': budget_name,
                            'current_percentage': percentage_used,
                            'action': 'Triggered alert',
                            'timestamp': datetime.now().isoformat()
                        })
                        
                        # Recommend specific actions based on budget
                        if 'development' in budget_name.lower():
                            controls['recommendations'].append({
                                'budget': budget_name,
                                'action': 'Stop non-essential development instances',
                                'priority': 'HIGH'
                            })
                        elif 'production' in budget_name.lower():
                            controls['recommendations'].append({
                                'budget': budget_name,
                                'action': 'Review production resource utilization',
                                'priority': 'CRITICAL'
                            })
            
            return controls
            
        except Exception as e:
            logger.error(f"Error enforcing cost controls: {e}")
            raise

# ============================================================================
# MAIN OPTIMIZATION ENGINE
# ============================================================================

class AWSCostOptimizationEngine:
    """Main engine coordinating all AWS cost optimization activities."""
    
    def __init__(self):
        self.account_manager = AWSMultiAccountManager("123456789012")
        self.optimization_results = {}
        self.total_potential_savings = 0
    
    async def run_comprehensive_optimization(self) -> Dict[str, Any]:
        """Run comprehensive cost optimization across all accounts."""
        print("=" * 80)
        print("AWS COST OPTIMIZATION ENGINE")
        print("=" * 80)
        
        optimization_report = {
            'execution_time': datetime.now().isoformat(),
            'accounts_analyzed': len(self.account_manager.accounts),
            'total_potential_savings': 0,
            'account_reports': {}
        }
        
        # Run optimization for each account
        for account_id, account in self.account_manager.accounts.items():
            print(f"\nAnalyzing account: {account.name} ({account_id})")
            
            account_report = await self._optimize_single_account(account)
            optimization_report['account_reports'][account_id] = account_report
            
            # Aggregate savings
            account_savings = account_report.get('total_potential_savings', 0)
            optimization_report['total_potential_savings'] += account_savings
        
        print(f"\n{'='*80}")
        print(f"OPTIMIZATION COMPLETE")
        print(f"Total potential savings: ${optimization_report['total_potential_savings']:,.2f}/month")
        print(f"Accounts analyzed: {optimization_report['accounts_analyzed']}")
        print(f"{'='*80}")
        
        # Generate executive summary
        optimization_report['executive_summary'] = self._generate_executive_summary(optimization_report)
        
        return optimization_report
    
    async def _optimize_single_account(self, account: AWSAccount) -> Dict[str, Any]:
        """Run optimization for a single AWS account."""
        account_report = {
            'account_id': account.account_id,
            'account_name': account.name,
            'analysis_timestamp': datetime.now().isoformat(),
            'total_potential_savings': 0,
            'optimization_categories': {}
        }
        
        try:
            # 1. Cost Analysis
            print("  • Analyzing costs...")
            ce_analyzer = CostExplorerAnalyzer(account)
            cost_data = await ce_analyzer.get_cost_and_usage(
                start_date=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
                end_date=datetime.now().strftime('%Y-%m-%d')
            )
            account_report['cost_analysis'] = cost_data
            
            # 2. Tagging Audit
            print("  • Auditing resource tags...")
            tagging_strategy = TaggingStrategy(account)
            tag_audit = await tagging_strategy.audit_resource_tags()
            account_report['optimization_categories']['tagging'] = tag_audit
            
            # 3. Reserved Instance Optimization
            print("  • Analyzing Reserved Instances...")
            ri_optimizer = ReservedInstanceOptimizer(account)
            ri_analysis = await ri_optimizer.analyze_ri_coverage()
            account_report['optimization_categories']['reserved_instances'] = ri_analysis
            
            # Calculate RI savings
            ri_savings = sum(
                rec.get('estimated_savings', 0)
                for rec in ri_analysis.get('recommendations', [])
                if 'estimated_savings' in rec
            )
            
            # 4. Savings Plans Analysis
            print("  • Analyzing Savings Plans...")
            sp_analyzer = SavingsPlansAnalyzer(account)
            sp_analysis = await sp_analyzer.analyze_savings_plans()
            account_report['optimization_categories']['savings_plans'] = sp_analysis
            
            # 5. Idle Resource Detection
            print("  • Detecting idle resources...")
            idle_detector = IdleResourceDetector(account)
            idle_ec2 = await idle_detector.detect_idle_ec2_instances()
            idle_ebs = await idle_detector.detect_unattached_ebs_volumes()
            idle_eips = await idle_detector.detect_unused_elastic_ips()
            
            idle_resources = {
                'idle_ec2_instances': idle_ec2,
                'unattached_ebs_volumes': idle_ebs,
                'unused_elastic_ips': idle_eips
            }
            account_report['optimization_categories']['idle_resources'] = idle_resources
            
            # Calculate idle resource savings
            idle_savings = sum(
                inst.get('potential_savings', 0) for inst in idle_ec2
            ) + sum(
                vol.get('potential_savings', 0) for vol in idle_ebs
            ) + sum(
                eip.get('potential_savings', 0) for eip in idle_eips
            )
            
            # 6. Storage Optimization
            print("  • Optimizing storage...")
            storage_optimizer = StorageOptimizer(account)
            s3_analysis = await storage_optimizer.analyze_s3_storage()
            ebs_optimization = await storage_optimizer.optimize_ebs_storage()
            
            storage_optimization = {
                's3_analysis': s3_analysis,
                'ebs_optimization': ebs_optimization
            }
            account_report['optimization_categories']['storage'] = storage_optimization
            
            # Calculate storage savings
            storage_savings = sum(
                opp.get('estimated_monthly_savings', 0)
                for opp in s3_analysis.get('optimization_opportunities', [])
            ) + sum(
                opp.get('estimated_monthly_savings', 0)
                for opp in ebs_optimization
            )
            
            # 7. Network Cost Optimization
            print("  • Optimizing network costs...")
            network_optimizer = NetworkCostOptimizer(account)
            network_analysis = await network_optimizer.analyze_data_transfer_costs()
            vpc_endpoints = await network_optimizer.optimize_vpc_endpoints()
            
            network_optimization = {
                'data_transfer_analysis': network_analysis,
                'vpc_endpoint_recommendations': vpc_endpoints
            }
            account_report['optimization_categories']['network'] = network_optimization
            
            # Calculate network savings
            network_savings = sum(
                opp.get('estimated_savings', 0)
                for opp in network_analysis.get('optimization_opportunities', [])
            ) + sum(
                rec.get('estimated_monthly_savings', 0)
                for rec in vpc_endpoints
            )
            
            # 8. Budget Enforcement
            print("  • Analyzing budgets...")
            budget_enforcer = BudgetEnforcer(account)
            budget_analysis = await budget_enforcer.analyze_budgets()
            cost_controls = await budget_enforcer.enforce_cost_controls()
            
            budget_management = {
                'budget_analysis': budget_analysis,
                'cost_controls': cost_controls
            }
            account_report['optimization_categories']['budget'] = budget_management
            
            # 9. Total Potential Savings
            total_savings = ri_savings + idle_savings + storage_savings + network_savings
            account_report['total_potential_savings'] = total_savings
            
            print(f"  ✓ Analysis complete. Potential savings: ${total_savings:,.2f}/month")
            
        except Exception as e:
            logger.error(f"Error optimizing account {account.account_id}: {e}")
            account_report['error'] = str(e)
        
        return account_report
    
    def _generate_executive_summary(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """Generate executive summary from optimization report."""
        total_savings = report['total_potential_savings']
        account_count = report['accounts_analyzed']
        
        # Aggregate recommendations by priority
        recommendations = {
            'HIGH': [],
            'MEDIUM': [],
            'LOW': []
        }
        
        # Extract recommendations from all accounts
        for account_id, account_report in report['account_reports'].items():
            categories = account_report.get('optimization_categories', {})
            
            for category, data in categories.items():
                if isinstance(data, dict) and 'recommendations' in data:
                    for rec in data['recommendations']:
                        priority = rec.get('priority', 'MEDIUM')
                        recommendations[priority].append({
                            'account': account_id,
                            'category': category,
                            'action': rec.get('action', ''),
                            'details': rec.get('details', ''),
                            'savings': rec.get('estimated_savings', rec.get('estimated_monthly_savings', 0))
                        })
        
        summary = {
            'total_potential_monthly_savings': total_savings,
            'total_potential_annual_savings': total_savings * 12,
            'accounts_analyzed': account_count,
            'recommendation_count': {
                'high': len(recommendations['HIGH']),
                'medium': len(recommendations['MEDIUM']),
                'low': len(recommendations['LOW'])
            },
            'top_recommendations': sorted(
                recommendations['HIGH'],
                key=lambda x: x.get('savings', 0),
                reverse=True
            )[:5],
            'implementation_timeline': {
                'immediate': 'Stop idle resources, optimize storage',
                'short_term': 'Purchase Reserved Instances/Savings Plans',
                'medium_term': 'Implement tagging strategy, network optimization',
                'long_term': 'Architectural improvements, service catalog'
            }
        }
        
        return summary
    
    async def generate_optimization_report(self, report_data: Dict[str, Any]) -> str:
        """Generate comprehensive optimization report."""
        import json
        
        report = {
            'generated_at': datetime.now().isoformat(),
            'engine_version': '1.0.0',
            'summary': report_data.get('executive_summary', {}),
            'detailed_analysis': report_data
        }
        
        # Save to file
        filename = f"aws_cost_optimization_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        
        print(f"\nOptimization report saved to: {filename}")
        
        # Also generate a summary CSV
        await self._generate_summary_csv(report_data, filename.replace('.json', '_summary.csv'))
        
        return filename
    
    async def _generate_summary_csv(self, report_data: Dict[str, Any], filename: str):
        """Generate summary CSV report."""
        import csv
        
        rows = []
        
        # Add header
        header = ['Account ID', 'Account Name', 'Category', 'Action', 'Priority', 
                 'Estimated Monthly Savings', 'Implementation Effort', 'ROI Months']
        
        for account_id, account_report in report_data['account_reports'].items():
            account_name = account_report.get('account_name', 'Unknown')
            categories = account_report.get('optimization_categories', {})
            
            for category, data in categories.items():
                if isinstance(data, dict) and 'recommendations' in data:
                    for rec in data['recommendations']:
                        row = [
                            account_id,
                            account_name,
                            category,
                            rec.get('action', ''),
                            rec.get('priority', 'MEDIUM'),
                            rec.get('estimated_savings', rec.get('estimated_monthly_savings', 0)),
                            rec.get('effort', 'MEDIUM'),
                            rec.get('break_even_months', 12)
                        ]
                        rows.append(row)
        
        # Write CSV
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        
        print(f"Summary CSV saved to: {filename}")

# ============================================================================
# DEMONSTRATION
# ============================================================================

async def demonstrate_aws_cost_optimization():
    """Demonstrate AWS cost optimization capabilities."""
    print("=" * 80)
    print("AWS COST OPTIMIZATION INTEGRATION DEMONSTRATION")
    print("=" * 80)
    
    # Initialize the optimization engine
    engine = AWSCostOptimizationEngine()
    
    # Run comprehensive optimization
    print("\nRunning comprehensive cost optimization...")
    optimization_results = await engine.run_comprehensive_optimization()
    
    # Generate report
    print("\nGenerating optimization report...")
    report_file = await engine.generate_optimization_report(optimization_results)
    
    # Display key findings
    summary = optimization_results['executive_summary']
    
    print(f"\n{'='*80}")
    print("KEY FINDINGS")
    print(f"{'='*80}")
    print(f"Total Potential Monthly Savings: ${summary['total_potential_monthly_savings']:,.2f}")
    print(f"Total Potential Annual Savings: ${summary['total_potential_annual_savings']:,.2f}")
    print(f"Accounts Analyzed: {summary['accounts_analyzed']}")
    print(f"\nRecommendations by Priority:")
    print(f"  • HIGH: {summary['recommendation_count']['high']}")
    print(f"  • MEDIUM: {summary['recommendation_count']['medium']}")
    print(f"  • LOW: {summary['recommendation_count']['low']}")
    
    print(f"\nTop 5 Recommendations:")
    for i, rec in enumerate(summary['top_recommendations'], 1):
        print(f"  {i}. {rec.get('action', '')} (${rec.get('savings', 0):,.2f}/month)")
    
    print(f"\n{'='*80}")
    print("IMPLEMENTATION TIMELINE")
    print(f"{'='*80}")
    for timeframe, actions in summary['implementation_timeline'].items():
        print(f"  • {timeframe.title()}: {actions}")
    
    print(f"\n{'='*80}")
    print("DEMONSTRATION COMPLETE")
    print(f"Detailed report: {report_file}")
    print(f"{'='*80}")

# ============================================================================
# INTEGRATION WITH MICROAGENTS PLATFORM
# ============================================================================

class AWSCostOptimizationAgent:
    """MicroAgent for AWS cost optimization."""
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.engine = AWSCostOptimizationEngine()
        self.status = 'IDLE'
        self.last_run = None
    
    async def execute_optimization(self) -> Dict[str, Any]:
        """Execute cost optimization as a micro-agent."""
        self.status = 'RUNNING'
        
        try:
            results = await self.engine.run_comprehensive_optimization()
            
            self.status = 'COMPLETED'
            self.last_run = datetime.now()
            
            return {
                'agent_id': self.agent_id,
                'status': 'SUCCESS',
                'execution_time': self.last_run.isoformat(),
                'results': results['executive_summary'],
                'total_savings': results['total_potential_savings']
            }
            
        except Exception as e:
            self.status = 'ERROR'
            
            return {
                'agent_id': self.agent_id,
                'status': 'ERROR',
                'error': str(e),
                'execution_time': datetime.now().isoformat()
            }
    
    def get_status(self) -> Dict[str, Any]:
        """Get agent status."""
        return {
            'agent_id': self.agent_id,
            'status': self.status,
            'last_run': self.last_run.isoformat() if self.last_run else None,
            'capabilities': [
                'cost_explorer_analysis',
                'ri_optimization',
                'savings_plans_analysis',
                'idle_resource_detection',
                'storage_optimization',
                'network_cost_optimization',
                'budget_enforcement'
            ]
        }

# ============================================================================
# MAIN EXECUTION
# ============================================================================

async def main():
    """Main execution function."""
    try:
        # Run the demonstration
        await demonstrate_aws_cost_optimization()
        
        # Example of using the micro-agent
        print("\n" + "="*80)
        print("MICRO-AGENT INTEGRATION EXAMPLE")
        print("="*80)
        
        agent = AWSCostOptimizationAgent("aws-cost-agent-001")
        print(f"Agent created: {agent.agent_id}")
        print(f"Status: {agent.get_status()['status']}")
        
        # Simulate agent execution
        print("\nSimulating agent execution...")
        result = await agent.execute_optimization()
        
        print(f"Agent status: {agent.get_status()['status']}")
        print(f"Execution result: {result['status']}")
        
        if result['status'] == 'SUCCESS':
            print(f"Total identified savings: ${result['total_savings']:,.2f}/month")
        
    except Exception as e:
        logger.error(f"Error in AWS cost optimization: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())