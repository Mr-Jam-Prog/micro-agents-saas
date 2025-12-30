#!/bin/bash
# Gestionnaire de plugins pour MicroAgents CLI

set -euo pipefail

readonly PLUGIN_DIR="${MICROAGENTS_PLUGIN_DIR:-/plugins}"
readonly CONFIG_DIR="${MICROAGENTS_CONFIG_DIR:-/config}"
readonly CACHE_DIR="${MICROAGENTS_CACHE_DIR:-/tmp/.microagents/cache}"
readonly REGISTRY_URL="https://plugins.microagents.io/v1"
readonly LOCK_FILE="/tmp/.microagents/plugins.lock"

# Fonctions utilitaires
log() {
    echo "[$(date -Iseconds)] [plugin-manager] $*"
}

error() {
    echo "ERROR: $*" >&2
    exit 1
}

acquire_lock() {
    exec 200>"$LOCK_FILE"
    flock -n 200 || error "Another plugin operation is in progress"
    trap 'release_lock' EXIT
}

release_lock() {
    flock -u 200
    rm -f "$LOCK_FILE"
}

# Installation d'un plugin
install_plugin() {
    local plugin_name=$1
    local version=${2:-latest}
    
    acquire_lock
    
    log "Installing plugin: $plugin_name@$version"
    
    # Téléchargement depuis le registry
    local plugin_url="$REGISTRY_URL/plugins/$plugin_name/versions/$version/download"
    local plugin_file="$CACHE_DIR/$plugin_name-$version.tar.gz"
    
    mkdir -p "$CACHE_DIR"
    curl -s --fail -L -o "$plugin_file" "$plugin_url"
    
    # Vérification de l'intégrité
    local checksum_url="$plugin_url.sha256"
    local expected_checksum=$(curl -s --fail "$checksum_url")
    local actual_checksum=$(sha256sum "$plugin_file" | cut -d' ' -f1)
    
    if [ "$expected_checksum" != "$actual_checksum" ]; then
        error "Checksum mismatch for plugin $plugin_name"
    fi
    
    # Extraction et installation
    local target_dir="$PLUGIN_DIR/$plugin_name"
    mkdir -p "$target_dir"
    tar -xzf "$plugin_file" -C "$target_dir" --strip-components=1
    
    # Configuration
    if [ -f "$target_dir/config.yaml" ]; then
        cp "$target_dir/config.yaml" "$CONFIG_DIR/plugins/$plugin_name.yaml"
    fi
    
    # Installation des dépendances
    if [ -f "$target_dir/requirements.txt" ]; then
        pip install --user -r "$target_dir/requirements.txt"
    fi
    
    log "Plugin $plugin_name installed successfully"
    release_lock
}

# Liste des plugins installés
list_plugins() {
    if [ ! -d "$PLUGIN_DIR" ]; then
        echo "No plugins installed"
        return
    fi
    
    for plugin in "$PLUGIN_DIR"/*; do
        if [ -d "$plugin" ]; then
            local name=$(basename "$plugin")
            local version_file="$plugin/VERSION"
            local version="unknown"
            
            if [ -f "$version_file" ]; then
                version=$(cat "$version_file")
            fi
            
            echo "$name ($version)"
        fi
    done
}

# Mise à jour des plugins
update_plugins() {
    acquire_lock
    
    log "Updating all plugins..."
    
    for plugin_dir in "$PLUGIN_DIR"/*; do
        if [ -d "$plugin_dir" ]; then
            local plugin_name=$(basename "$plugin_dir")
            local current_version="unknown"
            local version_file="$plugin_dir/VERSION"
            
            if [ -f "$version_file" ]; then
                current_version=$(cat "$version_file")
            fi
            
            # Récupérer la dernière version
            local latest_version=$(curl -s "$REGISTRY_URL/plugins/$plugin_name/latest" | jq -r '.version')
            
            if [ "$latest_version" != "$current_version" ]; then
                log "Updating $plugin_name: $current_version -> $latest_version"
                install_plugin "$plugin_name" "$latest_version"
            fi
        fi
    done
    
    release_lock
}

# Gestion des arguments
case "${1:-}" in
    install)
        install_plugin "${2:?Plugin name required}" "${3:-latest}"
        ;;
    list)
        list_plugins
        ;;
    update)
        update_plugins
        ;;
    remove)
        local plugin_name="${2:?Plugin name required}"
        rm -rf "$PLUGIN_DIR/$plugin_name"
        rm -f "$CONFIG_DIR/plugins/$plugin_name.yaml"
        log "Plugin $plugin_name removed"
        ;;
    *)
        echo "Usage: $0 {install|list|update|remove} [plugin_name] [version]"
        exit 1
        ;;
esac