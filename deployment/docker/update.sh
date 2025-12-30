#!/bin/bash
# Script de mise à jour automatique du CLI

set -euo pipefail

readonly VERSION_FILE="/tmp/.microagents/version"
readonly LOCK_FILE="/tmp/.microagents/update.lock"
readonly LOG_FILE="/tmp/.microagents/update.log"
readonly BACKUP_DIR="/tmp/.microagents/backup"
readonly CURRENT_VERSION=$(microagents --version 2>/dev/null || echo "0.0.0")

# Fonction de logging
log() {
    echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE"
}

# Vérification de verrou pour éviter les mises à jour concurrentes
acquire_lock() {
    if [ -f "$LOCK_FILE" ]; then
        local pid=$(cat "$LOCK_FILE" 2>/dev/null)
        if ps -p "$pid" > /dev/null 2>&1; then
            log "Update already in progress (PID: $pid)"
            return 1
        fi
    fi
    echo $$ > "$LOCK_FILE"
    trap 'rm -f "$LOCK_FILE"' EXIT
}

# Récupération de la dernière version
get_latest_version() {
    local channel=${1:-stable}
    local url="https://api.microagents.io/v1/versions/cli/$channel"
    
    curl -s --fail \
        -H "Accept: application/json" \
        -H "User-Agent: MicroAgents-CLI-Updater/$CURRENT_VERSION" \
        "$url" | jq -r '.version'
}

# Téléchargement de la mise à jour
download_update() {
    local version=$1
    local url="https://downloads.microagents.io/cli/linux-amd64/microagents-$version"
    
    log "Downloading version $version..."
    curl -s --fail -L -o "/tmp/microagents-$version" "$url"
    
    # Vérification de l'intégrité
    local checksum_url="$url.sha256"
    local expected_checksum=$(curl -s --fail "$checksum_url")
    local actual_checksum=$(sha256sum "/tmp/microagents-$version" | cut -d' ' -f1)
    
    if [ "$expected_checksum" != "$actual_checksum" ]; then
        log "ERROR: Checksum mismatch for version $version"
        rm -f "/tmp/microagents-$version"
        return 1
    fi
    
    chmod +x "/tmp/microagents-$version"
    echo "/tmp/microagents-$version"
}

# Backup de la version actuelle
backup_current() {
    mkdir -p "$BACKUP_DIR"
    local backup_file="$BACKUP_DIR/microagents-$(date +%Y%m%d-%H%M%S)"
    
    log "Backing up current version to $backup_file"
    cp "$(which microagents)" "$backup_file"
    echo "$CURRENT_VERSION" > "$BACKUP_DIR/previous.version"
}

# Application de la mise à jour
apply_update() {
    local update_file=$1
    local target="/usr/local/bin/microagents"
    
    log "Applying update..."
    backup_current
    
    # Remplacement atomique
    cp "$update_file" "$target.tmp"
    mv "$target.tmp" "$target"
    
    # Mise à jour des complétions
    microagents --install-completion bash > /etc/bash_completion.d/microagents 2>/dev/null || true
    microagents --install-completion zsh > /etc/zsh/completion/_microagents 2>/dev/null || true
    
    # Nettoyage
    rm -f "$update_file"
    echo "$NEW_VERSION" > "$VERSION_FILE"
}

# Vérification de la nécessité de mise à jour
check_update_needed() {
    local latest=$1
    local current=$2
    
    # Conversion semantic version pour comparaison
    IFS='.' read -ra latest_parts <<< "$latest"
    IFS='.' read -ra current_parts <<< "$current"
    
    for i in {0..2}; do
        local l=${latest_parts[$i]:-0}
        local c=${current_parts[$i]:-0}
        
        if [ "$l" -gt "$c" ]; then
            return 0  # Mise à jour nécessaire
        elif [ "$l" -lt "$c" ]; then
            return 1  # Version plus récente déjà installée
        fi
    done
    return 1  # Versions identiques
}

# Fonction principale
main() {
    local channel=${1:-stable}
    local force=${2:-false}
    
    log "Starting update check (channel: $channel, current: $CURRENT_VERSION)"
    
    # Acquérir le verrou
    if ! acquire_lock; then
        exit 1
    fi
    
    # Récupérer la dernière version
    NEW_VERSION=$(get_latest_version "$channel")
    if [ -z "$NEW_VERSION" ]; then
        log "ERROR: Failed to fetch latest version"
        exit 1
    fi
    
    log "Latest version available: $NEW_VERSION"
    
    # Vérifier si une mise à jour est nécessaire
    if [ "$force" = "false" ] && ! check_update_needed "$NEW_VERSION" "$CURRENT_VERSION"; then
        log "Already on latest version ($CURRENT_VERSION)"
        exit 0
    fi
    
    # Télécharger et appliquer la mise à jour
    local update_file
    if update_file=$(download_update "$NEW_VERSION"); then
        apply_update "$update_file"
        log "Successfully updated to version $NEW_VERSION"
        
        # Notification (optionnel)
        if command -v notify-send >/dev/null 2>&1; then
            notify-send "MicroAgents CLI" "Updated to version $NEW_VERSION" -i dialog-information
        fi
    else
        log "ERROR: Failed to download update"
        exit 1
    fi
}

# Gestion des arguments
case "${1:-}" in
    --check)
        NEW_VERSION=$(get_latest_version "${2:-stable}")
        if check_update_needed "$NEW_VERSION" "$CURRENT_VERSION"; then
            echo "Update available: $CURRENT_VERSION -> $NEW_VERSION"
            exit 0
        else
            echo "Already up to date ($CURRENT_VERSION)"
            exit 1
        fi
        ;;
    --force)
        main "${2:-stable}" "true"
        ;;
    --channel)
        main "$2" "false"
        ;;
    --rollback)
        # Rollback à la version précédente
        if [ -f "$BACKUP_DIR/previous.version" ]; then
            PREV_VERSION=$(cat "$BACKUP_DIR/previous.version")
            log "Rolling back to version $PREV_VERSION"
            # Implémenter le rollback ici
        fi
        ;;
    *)
        main "stable" "false"
        ;;
esac