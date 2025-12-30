#!/bin/bash
# Script d'initialisation pour auto-completion et configuration

# Chargement de la completion bash
if [ -f /etc/bash_completion.d/microagents ] && [ -n "$BASH_VERSION" ]; then
    . /etc/bash_completion.d/microagents
fi

# Configuration de l'environnement
export MICROAGENTS_CONFIG_DIR=${MICROAGENTS_CONFIG_DIR:-/config}
export MICROAGENTS_PLUGIN_DIR=${MICROAGENTS_PLUGIN_DIR:-/plugins}
export MICROAGENTS_CACHE_DIR=${MICROAGENTS_CACHE_DIR:-/tmp/.microagents/cache}

# Création des répertoires si inexistants
mkdir -p "$MICROAGENTS_CONFIG_DIR" "$MICROAGENTS_PLUGIN_DIR" "$MICROAGENTS_CACHE_DIR"

# Vérification de mise à jour (une fois par jour)
if [ -z "$MICROAGENTS_NO_UPDATE_CHECK" ]; then
    readonly UPDATE_CHECK_FILE="$MICROAGENTS_CACHE_DIR/last_update_check"
    readonly NOW=$(date +%s)
    readonly ONE_DAY=86400
    
    if [ ! -f "$UPDATE_CHECK_FILE" ] || [ $((NOW - $(cat "$UPDATE_CHECK_FILE"))) -gt $ONE_DAY ]; then
        if command -v update-microagents >/dev/null 2>&1; then
            update-microagents --check >/dev/null 2>&1 && echo "Update available for MicroAgents CLI"
        fi
        echo "$NOW" > "$UPDATE_CHECK_FILE"
    fi
fi

# Alias utiles
alias ma='microagents'
alias ma-status='microagents status'
alias ma-config='microagents config list'

# Fonction pour le mode offline
microagents-offline() {
    export MICROAGENTS_OFFLINE_MODE=true
    microagents config set offline.enabled true
    echo "Offline mode enabled"
}

microagents-online() {
    unset MICROAGENTS_OFFLINE_MODE
    microagents config set offline.enabled false
    echo "Offline mode disabled"
}

# Auto-completion pour zsh
if [ -n "$ZSH_VERSION" ]; then
    autoload -Uz compinit
    compinit
    if [ -f /etc/zsh/completion/_microagents ]; then
        . /etc/zsh/completion/_microagents
    fi
fi