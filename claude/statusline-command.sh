#!/usr/bin/env bash
# Claude Code status line
# Order: current directory | model | context remaining % | 5h/weekly usage limits

input=$(cat)

current_dir=$(echo "$input" | jq -r '.workspace.current_dir // .cwd // empty')
dir_name=$(basename "$current_dir")
[ -z "$dir_name" ] && dir_name="?"

model=$(echo "$input" | jq -r '.model.display_name // "?"')

remaining=$(echo "$input" | jq -r '.context_window.remaining_percentage // empty')
if [ -n "$remaining" ]; then
    ctx_info=$(printf 'Ctx %.0f%% left' "$remaining")
else
    ctx_info="Ctx n/a"
fi

five_hour=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
seven_day=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')

limit_info=""
if [ -n "$five_hour" ]; then
    limit_info="5h:$(printf '%.0f' "$five_hour")%"
fi
if [ -n "$seven_day" ]; then
    if [ -n "$limit_info" ]; then
        limit_info="$limit_info 7d:$(printf '%.0f' "$seven_day")%"
    else
        limit_info="7d:$(printf '%.0f' "$seven_day")%"
    fi
fi
[ -z "$limit_info" ] && limit_info="limits n/a"

printf "\033[2;36m%s\033[0m \033[2m|\033[0m \033[2;34m%s\033[0m \033[2m|\033[0m \033[2;33m%s\033[0m \033[2m|\033[0m \033[2;35m%s\033[0m" \
    "$dir_name" \
    "$model" \
    "$ctx_info" \
    "$limit_info"
