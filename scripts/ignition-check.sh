#!/usr/bin/env bash
# Renders the Ignition configs of every host in ansible/inventory.yaml, both
# the installer and the node config, with throwaway credentials, then checks
# them with butane --strict, ignition-validate and kubeadm config validate.
# make config runs the same playbook, so this renders what an install serves.
# See docs/development/contributing.md#checks
#
#   make ignition-check
#
# Needs docker, podman or Apple's container CLI (CONTAINER=<cli> picks one).
# The kubeadm binary is the inventory's kubernetes_version from dl.k8s.io,
# verified against its published sha256 and cached between runs.
set -euo pipefail

# Renovate bumps these through the custom manager for this file in renovate.json.
BUTANE_IMAGE="quay.io/coreos/butane:v2.28.0@sha256:143e7efd568e46e6f2bcb6f4250d01b0b49227232cc21a7150f8d39e577b9ea5"
IGNITION_VALIDATE_IMAGE="quay.io/coreos/ignition-validate:v2.28.0@sha256:3b21a2271b4cfe295bc5fea25f61ab732d0578654863971013fb47a528ca836a"

repo=$(cd "$(dirname "$0")/.." && pwd -P)
inventory="$repo/ansible/inventory.yaml"

if [ -z "${CONTAINER:-}" ]; then
  for cli in docker podman container; do
    if command -v "$cli" >/dev/null; then CONTAINER=$cli; break; fi
  done
fi
[ -n "${CONTAINER:-}" ] || { echo "ERROR: no container CLI found -- install docker, podman or container, or set CONTAINER" >&2; exit 1; }

# Apple's container CLI maps file ownership itself and has no --quiet.
case "$(basename "$CONTAINER")" in
  container) run_flags=(run --rm --progress none) ;;
  *) run_flags=(run --rm --quiet --user "$(id -u):$(id -g)") ;;
esac

kubernetes_version=$(awk -F'"' '/^ *kubernetes_version:/{print $2; exit}' "$inventory")
[ -n "$kubernetes_version" ] || { echo "ERROR: no kubernetes_version in $inventory" >&2; exit 1; }

work=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/ignition-check.XXXXXX")" && pwd -P)
cleanup() {
  if [ "$1" -eq 0 ]; then rm -rf "$work"; else echo "Renders kept in $work" >&2; fi
}
trap 'cleanup $?' EXIT

# butane on PATH for the playbook's transpile task, at the pinned version.
mkdir -p "$work/bin"
cat > "$work/bin/butane" <<EOF
#!/usr/bin/env bash
HOME="$HOME" exec "$CONTAINER" ${run_flags[*]} -v "$work:$work" "$BUTANE_IMAGE" "\$@"
EOF
chmod +x "$work/bin/butane"

# Throwaway stand-ins for ~/.ssh and output/: the templates read the user's
# public key, and make download's sysext only for its checksum.
mkdir -p "$work/home/.ssh" "$work/join/http" "$work/init/http"
ssh-keygen -q -t ed25519 -N "" -C ignition-check -f "$work/home/.ssh/id_ed25519"
sysext="kubernetes-$kubernetes_version-x86-64.raw"
touch "$work/join/http/$sysext" "$work/init/http/$sysext"

playbook() {
  (cd "$repo" && PATH="$work/bin:$PATH" uv run env HOME="$work/home" \
    ansible-playbook -i ansible/inventory.yaml ansible/playbooks/config.yaml \
    --skip-tags renew_join_credentials "$@")
}

echo "== Rendering every host, as make config does"
playbook -e "output_dir=$work/join"

# The first control plane renders differently while bootstrap_cluster is true:
# it runs kubeadm init. Same credentials, so known_hosts still resolves.
echo "== Rendering the first control plane with bootstrap_cluster=true"
cp -R "$work/join/credentials" "$work/init/credentials"
playbook -e "output_dir=$work/init" -e bootstrap_cluster=true --limit 'control_plane[0]'

echo "== ignition-validate ($IGNITION_VALIDATE_IMAGE)"
for config in "$work"/*/http/ignition-*.json "$work"/join/reprovision/ignition-*.json; do
  echo "${config#"$work"/}"
  "$CONTAINER" "${run_flags[@]}" -v "$work:$work:ro" "$IGNITION_VALIDATE_IMAGE" "$config"
done

echo "== reprovision configs write no path flatcar-reset keeps"
(cd "$repo" && uv run python scripts/reprovision-overlap.py ansible/playbooks/vars/reprovision.yaml "$work"/join/reprovision/ignition-*.json)

echo "== kubeadm config validate ($kubernetes_version)"
case "$(uname -m)" in
  x86_64 | amd64) arch=amd64 ;;
  arm64 | aarch64) arch=arm64 ;;
  *) echo "ERROR: no kubeadm build for $(uname -m)" >&2; exit 1 ;;
esac
cache="${XDG_CACHE_HOME:-$HOME/.cache}/homelab/kubeadm-$kubernetes_version-linux-$arch"
if [ ! -x "$cache/kubeadm" ]; then
  url="https://dl.k8s.io/release/$kubernetes_version/bin/linux/$arch/kubeadm"
  mkdir -p "$cache"
  curl -fsSL -o "$work/kubeadm" "$url"
  expected=$(curl -fsSL "$url.sha256")
  actual=$(shasum -a 256 "$work/kubeadm" | cut -d' ' -f1)
  [ "$expected" = "$actual" ] || { echo "ERROR: kubeadm sha256 $actual, dl.k8s.io publishes $expected" >&2; exit 1; }
  chmod +x "$work/kubeadm"
  mv "$work/kubeadm" "$cache/kubeadm"
fi
for config in "$work"/*/tmp/kubeadm/kubeadm-config-*.yaml; do
  echo "${config#"$work"/}"
  if [ "$(uname -s)" = Linux ]; then
    "$cache/kubeadm" config validate --config "$config"
  else
    # Any Linux image runs the static binary; the butane one is already pulled.
    "$CONTAINER" "${run_flags[@]}" -v "$work:$work:ro" -v "$cache:/kubeadm:ro" \
      --entrypoint /kubeadm/kubeadm "$BUTANE_IMAGE" config validate --config "$config"
  fi
done

echo "== All Ignition and kubeadm configs valid"
