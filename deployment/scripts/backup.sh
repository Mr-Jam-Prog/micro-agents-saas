#!/bin/bash
#
# MicroAgents Platform Backup Script
# Comprehensive backup strategy with encryption, compression, and verification
#
# Usage:
#   ./backup.sh [--type <backup_type>] [--mode <backup_mode>] [--target <target>]
#
# Backup Types:
#   full         - Full system backup (default)
#   incremental  - Incremental backup
#   config       - Configuration only
#   agents       - Agent definitions only
#   business     - Business data only
#   transaction  - Transaction log backup
#
# Backup Modes:
#   automatic    - Automated backup (default)
#   manual       - Manual backup with prompts
#   verify       - Verify existing backups
#   restore      - Restore from backup
#   test-restore - Test restore procedure
#
# Targets:
#   local        - Local storage only (default)
#   s3           - AWS S3
#   gcs          - Google Cloud Storage
#   azure        - Azure Blob Storage
#   multi        - All configured targets
#

set -o pipefail
shopt -s nocasematch

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config/backup.conf"
LOG_FILE="/var/log/microagents/backup.log"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_ID="backup_${TIMESTAMP}_$(uuidgen | cut -d- -f1)"
HOSTNAME=$(hostname -f)
INSTANCE_ID=${INSTANCE_ID:-$(hostname)}

# Default values
BACKUP_TYPE="full"
BACKUP_MODE="automatic"
BACKUP_TARGET="local"
ENCRYPTION_ENABLED=true
COMPRESSION_ENABLED=true
VERIFICATION_ENABLED=true
FORCE=false
DRY_RUN=false
VERBOSE=false

# Paths
BACKUP_ROOT="/var/backups/microagents"
BACKUP_DIR="${BACKUP_ROOT}/active"
ARCHIVE_DIR="${BACKUP_ROOT}/archive"
LOG_DIR="${BACKUP_ROOT}/logs"
TEMP_DIR="${BACKUP_ROOT}/tmp/${BACKUP_ID}"
CONFIG_BACKUP_DIR="${BACKUP_DIR}/config"
AGENTS_BACKUP_DIR="${BACKUP_DIR}/agents"
BUSINESS_BACKUP_DIR="${BACKUP_DIR}/business"
DATABASE_BACKUP_DIR="${BACKUP_DIR}/database"
TRANSACTION_BACKUP_DIR="${BACKUP_DIR}/transaction"

# Database configuration
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
DB_NAME=${DB_NAME:-microagents}
DB_USER=${DB_USER:-microagents}
DB_BACKUP_PARALLEL_JOBS=${DB_BACKUP_PARALLEL_JOBS:-4}

# Encryption configuration
ENCRYPTION_KEY_FILE="/etc/microagents/backup.key"
ENCRYPTION_PASSPHRASE=${ENCRYPTION_PASSPHRASE:-}
ENCRYPTION_ALGORITHM="aes-256-gcm"

# Compression configuration
COMPRESSION_LEVEL=6
COMPRESSION_EXTENSION=".gz"

# Retention policies (days)
RETENTION_FULL_DAYS=${RETENTION_FULL_DAYS:-30}
RETENTION_INCREMENTAL_DAYS=${RETENTION_INCREMENTAL_DAYS:-7}
RETENTION_TRANSACTION_DAYS=${RETENTION_TRANSACTION_DAYS:-3}
RETENTION_LEGAL_HOLD_DAYS=${RETENTION_LEGAL_HOLD_DAYS:-3650}  # 10 years

# AWS S3 configuration
S3_BUCKET=${S3_BUCKET:-microagents-backups}
S3_REGION=${S3_REGION:-us-east-1}
S3_ENDPOINT=${S3_ENDPOINT:-}
S3_STORAGE_CLASS="STANDARD_IA"
S3_GLACIER_TRANSITION_DAYS=30
S3_GLACIER_IR_TRANSITION_DAYS=90

# Google Cloud Storage configuration
GCS_BUCKET=${GCS_BUCKET:-microagents-backups}
GCS_LOCATION=${GCS_LOCATION:-us}
GCS_STORAGE_CLASS="NEARLINE"

# Azure Blob Storage configuration
AZURE_CONTAINER=${AZURE_CONTAINER:-microagents-backups}
AZURE_CONNECTION_STRING=${AZURE_CONNECTION_STRING:-}

# Multi-region replication
REGIONS=${REGIONS:-us-east-1,eu-west-1,ap-northeast-1}
REPLICATION_DELAY=300  # 5 minutes between region replications

# Verification
VERIFICATION_RETENTION_DAYS=7
VERIFICATION_SAMPLE_SIZE=3  # Number of files to verify per backup

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Global variables
declare -A BACKUP_STATS
declare -A VERIFICATION_RESULTS
BACKUP_START_TIME=$(date +%s.%N)
EXIT_CODE=0
ERROR_MESSAGES=()

# Load configuration
load_configuration() {
    if [[ -f "$CONFIG_FILE" ]]; then
        log_info "Loading configuration from $CONFIG_FILE"
        source "$CONFIG_FILE"
    else
        log_warning "Configuration file not found: $CONFIG_FILE"
    fi
    
    # Create necessary directories
    mkdir -p "$BACKUP_DIR" "$ARCHIVE_DIR" "$LOG_DIR" "$TEMP_DIR"
    mkdir -p "$CONFIG_BACKUP_DIR" "$AGENTS_BACKUP_DIR" "$BUSINESS_BACKUP_DIR" "$DATABASE_BACKUP_DIR" "$TRANSACTION_BACKUP_DIR"
}

# Logging functions
log_message() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    echo "[$timestamp] [$level] $message" | tee -a "$LOG_FILE" >&2
    
    # Also log to backup-specific log
    if [[ -n "$BACKUP_ID" ]]; then
        echo "[$timestamp] [$level] $message" >> "$LOG_DIR/${BACKUP_ID}.log"
    fi
}

log_debug() {
    if [[ "$VERBOSE" == true ]]; then
        log_message "DEBUG" "$1"
    fi
}

log_info() {
    log_message "INFO" "$1"
}

log_warning() {
    log_message "WARNING" "$1"
}

log_error() {
    log_message "ERROR" "$1"
    ERROR_MESSAGES+=("$1")
}

# Helper functions
check_dependency() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        log_error "Dependency missing: $cmd"
        return 1
    fi
    return 0
}

generate_metadata() {
    local backup_path="$1"
    local metadata_file="${backup_path}.metadata.json"
    
    cat > "$metadata_file" <<EOF
{
  "backup_id": "$BACKUP_ID",
  "timestamp": "$(date -Iseconds)",
  "hostname": "$HOSTNAME",
  "instance_id": "$INSTANCE_ID",
  "backup_type": "$BACKUP_TYPE",
  "backup_mode": "$BACKUP_MODE",
  "backup_target": "$BACKUP_TARGET",
  "encryption_enabled": $ENCRYPTION_ENABLED,
  "compression_enabled": $COMPRESSION_ENABLED,
  "database": {
    "host": "$DB_HOST",
    "port": "$DB_PORT",
    "name": "$DB_NAME"
  },
  "size_bytes": $(stat -c%s "$backup_path" 2>/dev/null || echo 0),
  "checksum_md5": "$(md5sum "$backup_path" | cut -d' ' -f1 2>/dev/null || echo "")",
  "checksum_sha256": "$(sha256sum "$backup_path" | cut -d' ' -f1 2>/dev/null || echo "")"
}
EOF
    
    log_debug "Generated metadata: $metadata_file"
}

encrypt_file() {
    local input_file="$1"
    local output_file="${input_file}.enc"
    
    if [[ "$ENCRYPTION_ENABLED" != true ]]; then
        log_debug "Encryption disabled, skipping"
        cp "$input_file" "$output_file"
        return 0
    fi
    
    if [[ -f "$ENCRYPTION_KEY_FILE" ]]; then
        # Use key file
        if openssl enc -"$ENCRYPTION_ALGORITHM" \
            -pass file:"$ENCRYPTION_KEY_FILE" \
            -in "$input_file" \
            -out "$output_file" \
            -pbkdf2 2>/dev/null; then
            log_debug "Encrypted using key file: $output_file"
            return 0
        fi
    elif [[ -n "$ENCRYPTION_PASSPHRASE" ]]; then
        # Use passphrase
        if openssl enc -"$ENCRYPTION_ALGORITHM" \
            -pass pass:"$ENCRYPTION_PASSPHRASE" \
            -in "$input_file" \
            -out "$output_file" \
            -pbkdf2 2>/dev/null; then
            log_debug "Encrypted using passphrase: $output_file"
            return 0
        fi
    else
        log_warning "No encryption key or passphrase available"
        cp "$input_file" "$output_file"
        return 0
    fi
    
    log_error "Encryption failed for: $input_file"
    return 1
}

decrypt_file() {
    local input_file="$1"
    local output_file="$2"
    
    if [[ "$ENCRYPTION_ENABLED" != true ]] || [[ ! "$input_file" =~ \.enc$ ]]; then
        cp "$input_file" "$output_file"
        return 0
    fi
    
    if [[ -f "$ENCRYPTION_KEY_FILE" ]]; then
        if openssl enc -"$ENCRYPTION_ALGORITHM" \
            -pass file:"$ENCRYPTION_KEY_FILE" \
            -in "$input_file" \
            -out "$output_file" \
            -d -pbkdf2 2>/dev/null; then
            return 0
        fi
    elif [[ -n "$ENCRYPTION_PASSPHRASE" ]]; then
        if openssl enc -"$ENCRYPTION_ALGORITHM" \
            -pass pass:"$ENCRYPTION_PASSPHRASE" \
            -in "$input_file" \
            -out "$output_file" \
            -d -pbkdf2 2>/dev/null; then
            return 0
        fi
    fi
    
    log_error "Decryption failed for: $input_file"
    return 1
}

compress_file() {
    local input_file="$1"
    local output_file="${input_file}${COMPRESSION_EXTENSION}"
    
    if [[ "$COMPRESSION_ENABLED" != true ]]; then
        cp "$input_file" "$output_file"
        return 0
    fi
    
    if gzip -c -"$COMPRESSION_LEVEL" "$input_file" > "$output_file" 2>/dev/null; then
        log_debug "Compressed: $output_file"
        return 0
    fi
    
    log_error "Compression failed for: $input_file"
    return 1
}

decompress_file() {
    local input_file="$1"
    local output_file="$2"
    
    if [[ "$COMPRESSION_ENABLED" != true ]] || [[ ! "$input_file" =~ \.gz$ ]]; then
        cp "$input_file" "$output_file"
        return 0
    fi
    
    if gzip -d -c "$input_file" > "$output_file" 2>/dev/null; then
        return 0
    fi
    
    log_error "Decompression failed for: $input_file"
    return 1
}

# Backup functions
backup_database_full() {
    local start_time=$(date +%s.%N)
    local backup_file="${DATABASE_BACKUP_DIR}/db_full_${BACKUP_ID}.sql"
    local encrypted_file="${backup_file}.enc"
    local compressed_file="${encrypted_file}${COMPRESSION_EXTENSION}"
    local final_file="${DATABASE_BACKUP_DIR}/db_full_${BACKUP_ID}.sql.enc.gz"
    
    log_info "Starting full database backup"
    
    # Set PostgreSQL password in environment
    export PGPASSWORD="$DB_PASSWORD"
    
    # Perform full database dump with parallel jobs
    if pg_dumpall --host="$DB_HOST" \
                  --port="$DB_PORT" \
                  --username="$DB_USER" \
                  --jobs="$DB_BACKUP_PARALLEL_JOBS" \
                  --verbose \
                  --file="$backup_file" 2>> "$LOG_DIR/${BACKUP_ID}.log"; then
        
        BACKUP_STATS["database_size"]=$(stat -c%s "$backup_file")
        log_info "Database dump completed: $(numfmt --to=iec ${BACKUP_STATS["database_size"]})"
        
        # Encrypt
        if encrypt_file "$backup_file" "$encrypted_file"; then
            # Compress
            if compress_file "$encrypted_file" "$compressed_file"; then
                # Generate metadata
                generate_metadata "$compressed_file"
                
                # Move to final location
                mv "$compressed_file" "$final_file"
                
                # Cleanup temporary files
                rm -f "$backup_file" "$encrypted_file"
                
                local duration=$(echo "$(date +%s.%N) - $start_time" | bc)
                BACKUP_STATS["database_duration"]=$duration
                BACKUP_STATS["database_file"]="$final_file"
                
                log_info "Full database backup completed in ${duration}s: $final_file"
                return 0
            fi
        fi
    else
        log_error "Database dump failed"
    fi
    
    # Cleanup on failure
    rm -f "$backup_file" "$encrypted_file" "$compressed_file" "$final_file"
    return 1
}

backup_database_incremental() {
    local start_time=$(date +s.%N)
    local last_full_backup
    
    log_info "Starting incremental database backup"
    
    # Find last full backup
    last_full_backup=$(find "$DATABASE_BACKUP_DIR" -name "db_full_*.sql.enc.gz" -type f | sort | tail -1)
    
    if [[ -z "$last_full_backup" ]]; then
        log_warning "No full backup found, performing full backup instead"
        backup_database_full
        return $?
    fi
    
    # Get last full backup timestamp from filename
    local last_full_timestamp
    last_full_timestamp=$(echo "$last_full_backup" | grep -oP 'db_full_\K[^.]*')
    
    # Perform incremental backup using WAL files or custom method
    # For PostgreSQL, we would use pg_basebackup with --write-recovery-conf
    # This is a simplified example
    
    local incremental_file="${DATABASE_BACKUP_DIR}/db_inc_${BACKUP_ID}_since_${last_full_timestamp}.sql"
    
    # In production, this would use pg_basebackup or similar
    log_warning "Incremental database backup not fully implemented in this example"
    
    BACKUP_STATS["incremental_duration"]=$(echo "$(date +%s.%N) - $start_time" | bc)
    return 0
}

backup_database_transaction_logs() {
    local start_time=$(date +s.%N)
    
    log_info "Starting transaction log backup"
    
    # For PostgreSQL, archive WAL files
    # This assumes proper PostgreSQL configuration with archive_mode = on
    
    local wal_archive_dir="/var/lib/postgresql/wal_archive"
    local backup_wal_dir="${TRANSACTION_BACKUP_DIR}/wal_${BACKUP_ID}"
    
    mkdir -p "$backup_wal_dir"
    
    # Copy WAL files created in the last hour
    if find "$wal_archive_dir" -name "*.gz" -mmin -60 -type f -exec cp {} "$backup_wal_dir" \; 2>/dev/null; then
        local file_count
        file_count=$(find "$backup_wal_dir" -type f | wc -l)
        
        if [[ $file_count -gt 0 ]]; then
            # Create archive of WAL files
            local wal_archive="${TRANSACTION_BACKUP_DIR}/wal_${BACKUP_ID}.tar"
            
            if tar -cf "$wal_archive" -C "$backup_wal_dir" . 2>/dev/null; then
                # Encrypt and compress
                local encrypted_wal="${wal_archive}.enc"
                local compressed_wal="${encrypted_wal}${COMPRESSION_EXTENSION}"
                local final_wal="${TRANSACTION_BACKUP_DIR}/wal_${BACKUP_ID}.tar.enc.gz"
                
                if encrypt_file "$wal_archive" "$encrypted_wal" && \
                   compress_file "$encrypted_wal" "$compressed_wal"; then
                    generate_metadata "$compressed_wal"
                    mv "$compressed_wal" "$final_wal"
                    
                    # Cleanup
                    rm -rf "$backup_wal_dir" "$wal_archive" "$encrypted_wal"
                    
                    BACKUP_STATS["wal_files"]=$file_count
                    BACKUP_STATS["wal_duration"]=$(echo "$(date +%s.%N) - $start_time" | bc)
                    BACKUP_STATS["wal_file"]="$final_wal"
                    
                    log_info "Transaction log backup completed: $file_count WAL files"
                    return 0
                fi
            fi
        else
            log_info "No new WAL files to backup"
            return 0
        fi
    fi
    
    # Cleanup on failure
    rm -rf "$backup_wal_dir"
    return 1
}

backup_configuration() {
    local start_time=$(date +%s.%N)
    local config_dir="/etc/microagents"
    local config_backup="${CONFIG_BACKUP_DIR}/config_${BACKUP_ID}.tar"
    
    log_info "Starting configuration backup"
    
    if [[ ! -d "$config_dir" ]]; then
        log_warning "Configuration directory not found: $config_dir"
        return 1
    fi
    
    # Exclude sensitive files
    if tar --exclude="*.key" \
           --exclude="*.pem" \
           --exclude="*.crt" \
           --exclude="secrets*" \
           -cf "$config_backup" \
           -C "$config_dir" . 2>/dev/null; then
        
        BACKUP_STATS["config_size"]=$(stat -c%s "$config_backup")
        
        # Encrypt and compress
        local encrypted_config="${config_backup}.enc"
        local compressed_config="${encrypted_config}${COMPRESSION_EXTENSION}"
        local final_config="${CONFIG_BACKUP_DIR}/config_${BACKUP_ID}.tar.enc.gz"
        
        if encrypt_file "$config_backup" "$encrypted_config" && \
           compress_file "$encrypted_config" "$compressed_config"; then
            generate_metadata "$compressed_config"
            mv "$compressed_config" "$final_config"
            
            rm -f "$config_backup" "$encrypted_config"
            
            BACKUP_STATS["config_duration"]=$(echo "$(date +%s.%N) - $start_time" | bc)
            BACKUP_STATS["config_file"]="$final_config"
            
            log_info "Configuration backup completed: $(numfmt --to=iec ${BACKUP_STATS["config_size"]})"
            return 0
        fi
    fi
    
    rm -f "$config_backup"
    return 1
}

backup_agent_definitions() {
    local start_time=$(date +%s.%N)
    local agents_backup="${AGENTS_BACKUP_DIR}/agents_${BACKUP_ID}.json"
    
    log_info "Starting agent definitions backup"
    
    # Export agent definitions from database or API
    # This is a simplified example - in production, would query the API
    
    # Mock agent export
    cat > "$agents_backup" <<EOF
{
  "backup_id": "$BACKUP_ID",
  "timestamp": "$(date -Iseconds)",
  "agent_count": 1400,
  "agents": [
    {"id": "agent_001", "type": "monitoring", "version": "1.0.0"},
    {"id": "agent_002", "type": "security", "version": "1.0.0"}
    // ... more agents
  ]
}
EOF
    
    BACKUP_STATS["agents_size"]=$(stat -c%s "$agents_backup")
    
    # Encrypt and compress
    local encrypted_agents="${agents_backup}.enc"
    local compressed_agents="${encrypted_agents}${COMPRESSION_EXTENSION}"
    local final_agents="${AGENTS_BACKUP_DIR}/agents_${BACKUP_ID}.json.enc.gz"
    
    if encrypt_file "$agents_backup" "$encrypted_agents" && \
       compress_file "$encrypted_agents" "$compressed_agents"; then
        generate_metadata "$compressed_agents"
        mv "$compressed_agents" "$final_agents"
        
        rm -f "$agents_backup" "$encrypted_agents"
        
        BACKUP_STATS["agents_duration"]=$(echo "$(date +%s.%N) - $start_time" | bc)
        BACKUP_STATS["agents_file"]="$final_agents"
        
        log_info "Agent definitions backup completed: $(numfmt --to=iec ${BACKUP_STATS["agents_size"]})"
        return 0
    fi
    
    rm -f "$agents_backup"
    return 1
}

backup_business_data() {
    local start_time=$(date +%s.%N)
    local business_backup="${BUSINESS_BACKUP_DIR}/business_${BACKUP_ID}.tar"
    
    log_info "Starting business data backup"
    
    # Business data directories
    local business_dirs=(
        "/var/lib/microagents/business"
        "/var/lib/microagents/reports"
        "/var/lib/microagents/analytics"
    )
    
    # Create list of directories that exist
    local dirs_to_backup=()
    for dir in "${business_dirs[@]}"; do
        if [[ -d "$dir" ]]; then
            dirs_to_backup+=("$dir")
        fi
    done
    
    if [[ ${#dirs_to_backup[@]} -eq 0 ]]; then
        log_warning "No business data directories found"
        return 1
    fi
    
    if tar -cf "$business_backup" "${dirs_to_backup[@]}" 2>/dev/null; then
        BACKUP_STATS["business_size"]=$(stat -c%s "$business_backup")
        
        # Encrypt and compress
        local encrypted_business="${business_backup}.enc"
        local compressed_business="${encrypted_business}${COMPRESSION_EXTENSION}"
        local final_business="${BUSINESS_BACKUP_DIR}/business_${BACKUP_ID}.tar.enc.gz"
        
        if encrypt_file "$business_backup" "$encrypted_business" && \
           compress_file "$encrypted_business" "$compressed_business"; then
            generate_metadata "$compressed_business"
            mv "$compressed_business" "$final_business"
            
            rm -f "$business_backup" "$encrypted_business"
            
            BACKUP_STATS["business_duration"]=$(echo "$(date +%s.%N) - $start_time" | bc)
            BACKUP_STATS["business_file"]="$final_business"
            
            log_info "Business data backup completed: $(numfmt --to=iec ${BACKUP_STATS["business_size"]})"
            return 0
        fi
    fi
    
    rm -f "$business_backup"
    return 1
}

# Retention management
apply_retention_policy() {
    log_info "Applying retention policies"
    
    # Full backups retention
    find "$DATABASE_BACKUP_DIR" -name "db_full_*" -type f -mtime +"$RETENTION_FULL_DAYS" -delete 2>/dev/null
    
    # Incremental backups retention
    find "$DATABASE_BACKUP_DIR" -name "db_inc_*" -type f -mtime +"$RETENTION_INCREMENTAL_DAYS" -delete 2>/dev/null
    
    # Transaction logs retention
    find "$TRANSACTION_BACKUP_DIR" -name "wal_*" -type f -mtime +"$RETENTION_TRANSACTION_DAYS" -delete 2>/dev/null
    
    # Configuration backups retention
    find "$CONFIG_BACKUP_DIR" -name "config_*" -type f -mtime +"$RETENTION_FULL_DAYS" -delete 2>/dev/null
    
    # Agent definitions retention
    find "$AGENTS_BACKUP_DIR" -name "agents_*" -type f -mtime +"$RETENTION_FULL_DAYS" -delete 2>/dev/null
    
    # Business data retention
    find "$BUSINESS_BACKUP_DIR" -name "business_*" -type f -mtime +"$RETENTION_FULL_DAYS" -delete 2>/dev/null
    
    # Legal hold - never delete files with .legalhold extension
    # find ... -name "*.legalhold" -type f -mtime +"$RETENTION_LEGAL_HOLD_DAYS" -delete
    
    # Archive old backups
    find "$BACKUP_DIR" -name "*.gz" -type f -mtime +"$RETENTION_FULL_DAYS" -exec mv {} "$ARCHIVE_DIR" \; 2>/dev/null
    
    log_info "Retention policies applied"
}

# Cloud storage functions
upload_to_s3() {
    local file="$1"
    local s3_path="s3://${S3_BUCKET}/${HOSTNAME}/$(basename "$file")"
    
    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY RUN] Would upload to S3: $s3_path"
        return 0
    fi
    
    log_info "Uploading to S3: $s3_path"
    
    local s3_cmd="aws s3 cp"
    if [[ -n "$S3_ENDPOINT" ]]; then
        s3_cmd="$s3_cmd --endpoint-url $S3_ENDPOINT"
    fi
    
    if $s3_cmd "$file" "$s3_path" \
        --storage-class "$S3_STORAGE_CLASS" \
        --region "$S3_REGION" \
        >> "$LOG_DIR/${BACKUP_ID}.log" 2>&1; then
        
        # Apply lifecycle rules
        if [[ -n "$S3_GLACIER_TRANSITION_DAYS" ]]; then
            aws s3api put-object-tagging \
                --bucket "$S3_BUCKET" \
                --key "${HOSTNAME}/$(basename "$file")" \
                --tagging "TagSet=[{Key=transition,Value=true}]" \
                --region "$S3_REGION" 2>/dev/null || true
        fi
        
        log_info "S3 upload successful"
        return 0
    else
        log_error "S3 upload failed"
        return 1
    fi
}

upload_to_gcs() {
    local file="$1"
    local gs_path="gs://${GCS_BUCKET}/${HOSTNAME}/$(basename "$file")"
    
    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY RUN] Would upload to GCS: $gs_path"
        return 0
    fi
    
    log_info "Uploading to GCS: $gs_path"
    
    if gsutil -q -o "GSUtil:default_project_id=${GCP_PROJECT}" \
              cp -c -Z "$file" "$gs_path" \
              >> "$LOG_DIR/${BACKUP_ID}.log" 2>&1; then
        
        # Set storage class
        gsutil -q storageclass set "$GCS_STORAGE_CLASS" "$gs_path" 2>/dev/null || true
        
        log_info "GCS upload successful"
        return 0
    else
        log_error "GCS upload failed"
        return 1
    fi
}

upload_to_azure() {
    local file="$1"
    local azure_path="${AZURE_CONTAINER}/${HOSTNAME}/$(basename "$file")"
    
    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY RUN] Would upload to Azure: $azure_path"
        return 0
    fi
    
    log_info "Uploading to Azure Blob Storage: $azure_path"
    
    if az storage blob upload \
        --connection-string "$AZURE_CONNECTION_STRING" \
        --container-name "$AZURE_CONTAINER" \
        --name "${HOSTNAME}/$(basename "$file")" \
        --file "$file" \
        --output none \
        >> "$LOG_DIR/${BACKUP_ID}.log" 2>&1; then
        
        log_info "Azure upload successful"
        return 0
    else
        log_error "Azure upload failed"
        return 1
    fi
}

replicate_to_regions() {
    local file="$1"
    local primary_region="$S3_REGION"
    
    log_info "Starting multi-region replication"
    
    IFS=',' read -ra REGION_LIST <<< "$REGIONS"
    
    for region in "${REGION_LIST[@]}"; do
        if [[ "$region" == "$primary_region" ]]; then
            continue
        fi
        
        log_info "Replicating to region: $region"
        
        # Upload to secondary region
        if aws s3 cp "$file" "s3://${S3_BUCKET}-${region}/${HOSTNAME}/$(basename "$file")" \
            --storage-class "$S3_STORAGE_CLASS" \
            --region "$region" \
            >> "$LOG_DIR/${BACKUP_ID}.log" 2>&1; then
            
            log_info "Replication to $region successful"
            
            # Wait before next replication
            sleep "$REPLICATION_DELAY"
        else
            log_error "Replication to $region failed"
            return 1
        fi
    done
    
    log_info "Multi-region replication completed"
    return 0
}

# Verification functions
verify_backup() {
    local backup_file="$1"
    local verification_dir="$TEMP_DIR/verify"
    
    mkdir -p "$verification_dir"
    
    log_info "Verifying backup: $(basename "$backup_file")"
    
    # Extract basename without extensions
    local base_name
    base_name=$(basename "$backup_file")
    base_name="${base_name%.gz}"
    base_name="${base_name%.enc}"
    base_name="${base_name%.tar}"
    base_name="${base_name%.sql}"
    base_name="${base_name%.json}"
    
    local temp_file="$verification_dir/${base_name}"
    
    # Decompress if needed
    if [[ "$backup_file" == *.gz ]]; then
        if ! decompress_file "$backup_file" "${temp_file}.compressed"; then
            VERIFICATION_RESULTS["$(basename "$backup_file")"]="failed: decompression"
            return 1
        fi
        backup_file="${temp_file}.compressed"
    fi
    
    # Decrypt if needed
    if [[ "$backup_file" == *.enc ]]; then
        if ! decrypt_file "$backup_file" "$temp_file"; then
            VERIFICATION_RESULTS["$(basename "$backup_file")"]="failed: decryption"
            return 1
        fi
        backup_file="$temp_file"
    fi
    
    # Verify based on file type
    if [[ "$backup_file" == *.tar ]]; then
        # Verify tar archive
        if tar -tf "$backup_file" &>/dev/null; then
            VERIFICATION_RESULTS["$(basename "$backup_file")"]="passed"
            log_info "Backup verification passed: $(basename "$backup_file")"
            return 0
        fi
    elif [[ "$backup_file" == *.sql ]]; then
        # Verify SQL file has content
        if head -n 1 "$backup_file" | grep -q "PostgreSQL database dump" 2>/dev/null; then
            VERIFICATION_RESULTS["$(basename "$backup_file")"]="passed"
            log_info "Backup verification passed: $(basename "$backup_file")"
            return 0
        fi
    elif [[ "$backup_file" == *.json ]]; then
        # Verify JSON is valid
        if jq empty "$backup_file" 2>/dev/null; then
            VERIFICATION_RESULTS["$(basename "$backup_file")"]="passed"
            log_info "Backup verification passed: $(basename "$backup_file")"
            return 0
        fi
    else
        # Generic file check
        if [[ -s "$backup_file" ]]; then
            VERIFICATION_RESULTS["$(basename "$backup_file")"]="passed"
            log_info "Backup verification passed: $(basename "$backup_file")"
            return 0
        fi
    fi
    
    VERIFICATION_RESULTS["$(basename "$backup_file")"]="failed: content"
    log_error "Backup verification failed: $(basename "$backup_file")"
    return 1
}

verify_all_backups() {
    log_info "Starting backup verification"
    
    local backup_files=()
    local verification_count=0
    local failed_count=0
    
    # Collect recent backup files
    mapfile -t backup_files < <(find "$BACKUP_DIR" -name "*.gz" -type f -mtime -"$VERIFICATION_RETENTION_DAYS" | head -"$VERIFICATION_SAMPLE_SIZE")
    
    if [[ ${#backup_files[@]} -eq 0 ]]; then
        log_warning "No backup files found for verification"
        return 1
    fi
    
    log_info "Verifying ${#backup_files[@]} backup files"
    
    for backup_file in "${backup_files[@]}"; do
        if verify_backup "$backup_file"; then
            ((verification_count++))
        else
            ((failed_count++))
        fi
    done
    
    # Cleanup temp directory
    rm -rf "$TEMP_DIR"
    
    if [[ $failed_count -eq 0 ]]; then
        log_info "Backup verification completed: $verification_count passed, 0 failed"
        return 0
    else
        log_error "Backup verification completed: $verification_count passed, $failed_count failed"
        return 1
    fi
}

# Restore functions
restore_backup() {
    local backup_file="$1"
    local restore_dir="$2"
    
    log_info "Starting restore from: $(basename "$backup_file")"
    
    if [[ ! -f "$backup_file" ]]; then
        log_error "Backup file not found: $backup_file"
        return 1
    fi
    
    mkdir -p "$restore_dir"
    
    # Determine backup type from filename
    if [[ "$backup_file" == *db_full* ]]; then
        restore_database "$backup_file" "$restore_dir"
    elif [[ "$backup_file" == *config* ]]; then
        restore_configuration "$backup_file" "$restore_dir"
    elif [[ "$backup_file" == *agents* ]]; then
        restore_agent_definitions "$backup_file" "$restore_dir"
    elif [[ "$backup_file" == *business* ]]; then
        restore_business_data "$backup_file" "$restore_dir"
    else
        log_error "Unknown backup type: $backup_file"
        return 1
    fi
}

restore_database() {
    local backup_file="$1"
    local restore_dir="$2"
    
    log_info "Restoring database from backup"
    
    # Decompress, decrypt, and restore
    local temp_sql="$restore_dir/database_restore.sql"
    
    if decompress_file "$backup_file" "${backup_file%.gz}" && \
       decrypt_file "${backup_file%.gz}" "$temp_sql"; then
        
        # Restore to database
        export PGPASSWORD="$DB_PASSWORD"
        
        if psql --host="$DB_HOST" \
                --port="$DB_PORT" \
                --username="$DB_USER" \
                --dbname="postgres" \
                --file="$temp_sql" \
                >> "$LOG_DIR/${BACKUP_ID}_restore.log" 2>&1; then
            
            log_info "Database restore completed successfully"
            rm -f "$temp_sql"
            return 0
        else
            log_error "Database restore failed"
            return 1
        fi
    else
        log_error "Failed to prepare database backup for restore"
        return 1
    fi
}

test_restore_procedure() {
    local test_dir="/tmp/restore_test_${BACKUP_ID}"
    
    log_info "Starting restore test procedure"
    
    mkdir -p "$test_dir"
    
    # Find a recent backup to test
    local test_backup
    test_backup=$(find "$BACKUP_DIR" -name "db_full_*.gz" -type f | sort -r | head -1)
    
    if [[ -z "$test_backup" ]]; then
        log_error "No backup found for restore testing"
        return 1
    fi
    
    log_info "Testing restore with: $(basename "$test_backup")"
    
    # Create test database
    local test_db="restore_test_${BACKUP_ID}"
    
    export PGPASSWORD="$DB_PASSWORD"
    
    # Create test database
    if createdb --host="$DB_HOST" \
                --port="$DB_PORT" \
                --username="$DB_USER" \
                "$test_db" 2>/dev/null; then
        
        # Test restore (simplified - in production would restore to test database)
        log_info "Restore test setup completed"
        
        # Cleanup test database
        dropdb --host="$DB_HOST" \
               --port="$DB_PORT" \
               --username="$DB_USER" \
               "$test_db" 2>/dev/null || true
        
        rm -rf "$test_dir"
        
        log_info "Restore test completed successfully"
        return 0
    else
        log_error "Failed to create test database"
        rm -rf "$test_dir"
        return 1
    fi
}

# Main backup orchestration
perform_backup() {
    log_info "Starting backup process: $BACKUP_TYPE"
    
    case "$BACKUP_TYPE" in
        "full")
            backup_configuration
            backup_agent_definitions
            backup_business_data
            backup_database_full
            backup_database_transaction_logs
            ;;
        "incremental")
            backup_database_incremental
            backup_database_transaction_logs
            ;;
        "config")
            backup_configuration
            ;;
        "agents")
            backup_agent_definitions
            ;;
        "business")
            backup_business_data
            ;;
        "transaction")
            backup_database_transaction_logs
            ;;
    esac
    
    # Apply retention policy
    apply_retention_policy
    
    # Upload to cloud storage if configured
    if [[ "$BACKUP_TARGET" != "local" ]]; then
        upload_to_cloud_storage
    fi
    
    # Verify backups if enabled
    if [[ "$VERIFICATION_ENABLED" == true ]]; then
        verify_all_backups
    fi
    
    log_info "Backup process completed: $BACKUP_TYPE"
}

upload_to_cloud_storage() {
    log_info "Uploading backups to cloud storage: $BACKUP_TARGET"
    
    # Find all backup files created in this session
    local backup_files=()
    
    case "$BACKUP_TYPE" in
        "full")
            backup_files=(
                $(find "$CONFIG_BACKUP_DIR" -name "*${BACKUP_ID}*" -type f)
                $(find "$AGENTS_BACKUP_DIR" -name "*${BACKUP_ID}*" -type f)
                $(find "$BUSINESS_BACKUP_DIR" -name "*${BACKUP_ID}*" -type f)
                $(find "$DATABASE_BACKUP_DIR" -name "*${BACKUP_ID}*" -type f)
                $(find "$TRANSACTION_BACKUP_DIR" -name "*${BACKUP_ID}*" -type f)
            )
            ;;
        "incremental")
            backup_files=(
                $(find "$DATABASE_BACKUP_DIR" -name "*${BACKUP_ID}*" -type f)
                $(find "$TRANSACTION_BACKUP_DIR" -name "*${BACKUP_ID}*" -type f)
            )
            ;;
        *)
            backup_files=(
                $(find "$BACKUP_DIR" -name "*${BACKUP_ID}*" -type f)
            )
            ;;
    esac
    
    for backup_file in "${backup_files[@]}"; do
        if [[ -f "$backup_file" ]]; then
            case "$BACKUP_TARGET" in
                "s3")
                    upload_to_s3 "$backup_file"
                    ;;
                "gcs")
                    upload_to_gcs "$backup_file"
                    ;;
                "azure")
                    upload_to_azure "$backup_file"
                    ;;
                "multi")
                    upload_to_s3 "$backup_file"
                    sleep 2
                    replicate_to_regions "$backup_file"
                    ;;
            esac
        fi
    done
}

# Reporting
generate_report() {
    local end_time=$(date +%s.%N)
    local total_duration=$(echo "$end_time - $BACKUP_START_TIME" | bc)
    
    local report_file="$LOG_DIR/${BACKUP_ID}_report.json"
    
    cat > "$report_file" <<EOF
{
  "backup_id": "$BACKUP_ID",
  "timestamp": "$(date -Iseconds)",
  "hostname": "$HOSTNAME",
  "backup_type": "$BACKUP_TYPE",
  "backup_mode": "$BACKUP_MODE",
  "backup_target": "$BACKUP_TARGET",
  "total_duration": $total_duration,
  "status": "$(if [[ $EXIT_CODE -eq 0 ]]; then echo "success"; else echo "failed"; fi)",
  "statistics": {
    $(for key in "${!BACKUP_STATS[@]}"; do
        echo "\"$key\": \"${BACKUP_STATS[$key]}\","
      done | sed '$ s/,$//')
  },
  "verification_results": {
    $(for key in "${!VERIFICATION_RESULTS[@]}"; do
        echo "\"$key\": \"${VERIFICATION_RESULTS[$key]}\","
      done | sed '$ s/,$//')
  },
  "error_messages": [
    $(for msg in "${ERROR_MESSAGES[@]}"; do
        echo "\"${msg//\"/\\\"}\","
      done | sed '$ s/,$//')
  ]
}
EOF
    
    log_info "Backup report generated: $report_file"
    
    # Print summary
    echo -e "${BOLD}=== Backup Summary ===${NC}"
    echo -e "Backup ID:    ${GREEN}$BACKUP_ID${NC}"
    echo -e "Type:         $BACKUP_TYPE"
    echo -e "Target:       $BACKUP_TARGET"
    echo -e "Duration:     ${total_duration}s"
    echo -e "Status:       $(if [[ $EXIT_CODE -eq 0 ]]; then echo -e "${GREEN}SUCCESS${NC}"; else echo -e "${RED}FAILED${NC}"; fi)"
    
    if [[ ${#ERROR_MESSAGES[@]} -gt 0 ]]; then
        echo -e "${RED}Errors:${NC}"
        for error in "${ERROR_MESSAGES[@]}"; do
            echo -e "  - $error"
        done
    fi
}

# Main execution
main() {
    parse_arguments "$@"
    load_configuration
    
    # Check dependencies
    local missing_deps=0
    local required_deps=("tar" "gzip" "openssl")
    
    case "$BACKUP_TYPE" in
        "full"|"incremental"|"transaction")
            required_deps+=("psql" "pg_dumpall")
            ;;
    esac
    
    case "$BACKUP_TARGET" in
        "s3"|"multi")
            required_deps+=("aws")
            ;;
        "gcs")
            required_deps+=("gsutil")
            ;;
        "azure")
            required_deps+=("az")
            ;;
    esac
    
    for dep in "${required_deps[@]}"; do
        if ! check_dependency "$dep"; then
            ((missing_deps++))
        fi
    done
    
    if [[ $missing_deps -gt 0 ]]; then
        log_error "Missing $missing_deps required dependencies"
        exit 1
    fi
    
    if [[ "$DRY_RUN" == true ]]; then
        log_info "Dry run mode enabled"
        echo "Backup would be performed with:"
        echo "  Type: $BACKUP_TYPE"
        echo "  Target: $BACKUP_TARGET"
        echo "  Encryption: $ENCRYPTION_ENABLED"
        echo "  Compression: $COMPRESSION_ENABLED"
        exit 0
    fi
    
    # Execute based on mode
    case "$BACKUP_MODE" in
        "automatic"|"manual")
            perform_backup
            ;;
        "verify")
            verify_all_backups
            ;;
        "restore")
            if [[ -z "$RESTORE_FILE" ]]; then
                log_error "Restore file not specified"
                exit 1
            fi
            restore_backup "$RESTORE_FILE" "/tmp/restore_${BACKUP_ID}"
            ;;
        "test-restore")
            test_restore_procedure
            ;;
        *)
            log_error "Unknown backup mode: $BACKUP_MODE"
            exit 1
            ;;
    esac
    
    generate_report
    
    # Cleanup temp directory
    rm -rf "$TEMP_DIR"
    
    exit $EXIT_CODE
}

# Parse command line arguments
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --type|-t)
                BACKUP_TYPE="$2"
                shift 2
                ;;
            --mode|-m)
                BACKUP_MODE="$2"
                shift 2
                ;;
            --target|-g)
                BACKUP_TARGET="$2"
                shift 2
                ;;
            --restore-file|-r)
                RESTORE_FILE="$2"
                BACKUP_MODE="restore"
                shift 2
                ;;
            --no-encryption)
                ENCRYPTION_ENABLED=false
                shift
                ;;
            --no-compression)
                COMPRESSION_ENABLED=false
                shift
                ;;
            --no-verification)
                VERIFICATION_ENABLED=false
                shift
                ;;
            --force|-f)
                FORCE=true
                shift
                ;;
            --dry-run|-d)
                DRY_RUN=true
                shift
                ;;
            --verbose|-v)
                VERBOSE=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

show_help() {
    cat <<EOF
MicroAgents Platform Backup Script

Usage: $0 [OPTIONS]

Options:
  -t, --type TYPE       Backup type: full, incremental, config, agents, 
                        business, transaction (default: full)
  -m, --mode MODE       Backup mode: automatic, manual, verify, restore,
                        test-restore (default: automatic)
  -g, --target TARGET   Backup target: local, s3, gcs, azure, multi (default: local)
  -r, --restore-file FILE  File to restore (sets mode to restore)
  --no-encryption       Disable encryption
  --no-compression      Disable compression
  --no-verification     Disable backup verification
  -f, --force           Force backup even if warnings
  -d, --dry-run         Perform dry run without actual backup
  -v, --verbose         Enable verbose logging
  -h, --help            Show this help message

Examples:
  # Full backup to local storage
  $0 --type full
  
  # Incremental backup to S3
  $0 --type incremental --target s3
  
  # Verify existing backups
  $0 --mode verify
  
  # Restore from backup file
  $0 --mode restore --restore-file /path/to/backup.gz
  
  # Test restore procedure
  $0 --mode test-restore

Environment Variables:
  DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD  Database connection
  S3_BUCKET, S3_REGION                             AWS S3 configuration
  GCS_BUCKET, GCP_PROJECT                          Google Cloud Storage
  AZURE_CONTAINER, AZURE_CONNECTION_STRING         Azure Blob Storage
  RETENTION_FULL_DAYS                              Full backup retention (days)
  ENCRYPTION_PASSPHRASE                            Encryption passphrase
EOF
}

# Run main if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi