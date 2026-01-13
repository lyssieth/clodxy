# fish completion for clodxy

# Main options
complete -c clodxy -f -l help -d "Show help"
complete -c clodxy -f -l version -d "Show version"
complete -c clodxy -f -l list -d "List available backends and models"
complete -c clodxy -f -l completions -d "Print shell completion code"
complete -c clodxy -f -l validate-config -d "Validate configuration file"

# Backend and model from config (clodxy validates combinations)
complete -c clodxy -f -l backend -d "Backend to use" -a "(cat ~/.config/clodxy/config.json | jq -r '.backends | keys[]' 2>/dev/null)"
complete -c clodxy -f -l model -d "Model to use" -a "(cat ~/.config/clodxy/config.json | jq -r '.backends | map(.models | keys) | add | unique[]' 2>/dev/null)"

complete -c clodxy -f -l host -d "Proxy host" -a "127.0.0.1 localhost"
complete -c clodxy -f -l port -d "Proxy port" -a "0 8080 9000 11434"

# Passthrough args for claude
complete -c clodxy -f -a "--"

# Hidden short options
complete -c clodxy -s h -l help
