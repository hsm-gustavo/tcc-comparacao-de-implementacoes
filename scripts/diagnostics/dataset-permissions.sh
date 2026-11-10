#!/usr/bin/env bash
# Shared by the diagnostic runners. Keep private results owned by sudo's caller.
dataset_sudo_owner() {
  if (( EUID == 0 )) && [[ "${SUDO_UID:-}" =~ ^[0-9]+$ && "${SUDO_GID:-}" =~ ^[0-9]+$ ]]; then
    printf '%s:%s' "$SUDO_UID" "$SUDO_GID"
  fi
  return 0
}

dataset_prepare_parent() {
  local root_dir="$1" output_dir="$2" owner directory part relative
  local -a directories parts
  owner="$(dataset_sudo_owner)"
  # Repair each ancestor before descending. A root-owned datasets/ with mode
  # 700 hides even a correctly owned run directory. Do not recurse into old runs.
  directory="$root_dir/experiments/datasets"
  relative="$(dirname "$output_dir")"
  [[ "$relative" == "$directory" || "$relative" == "$directory/"* ]] || {
    echo '[datasets] Output must be inside experiments/datasets' >&2; return 1;
  }
  directories=("$root_dir/experiments" "$directory")
  if [[ "$relative" != "$directory" ]]; then
    relative="${relative#"$directory/"}"
    IFS=/ read -r -a parts <<<"$relative"
    for part in "${parts[@]}"; do
      [[ -n "$part" && "$part" != . && "$part" != .. ]] || return 1
      directory="$directory/$part"
      directories+=("$directory")
    done
  fi
  for directory in "${directories[@]}"; do
    if [[ -L "$directory" ]]; then
      echo "[datasets] Refusing a symlink in the output hierarchy: $directory" >&2
      return 1
    fi
    mkdir -p -- "$directory" || return 1
    if [[ -n "$owner" ]]; then
      chown -- "$owner" "$directory" || return 1
      chmod u+rwx -- "$directory" || return 1
    fi
  done
}

dataset_return_ownership() {
  local owner directory
  owner="$(dataset_sudo_owner)"
  [[ -n "$owner" ]] || return 0
  for directory in "$@"; do
    [[ -n "$directory" ]] || continue
    if [[ -L "$directory" ]]; then
      echo "[datasets] Refusing to change ownership through a symlink: $directory" >&2
      return 1
    fi
    [[ -d "$directory" ]] || continue
    chown -R -P --no-dereference -- "$owner" "$directory" || return 1
    # Only add owner access; do not make private SQL/logs world-readable.
    # chmod -R does not follow symlinks encountered inside these directories.
    chmod -R u+rwX -- "$directory" || return 1
  done
}
