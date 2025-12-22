# yaml-language-server: $schema=<https://json.schemastore.org/kustomization.json>

---

# K10 Backup Policies

# These policies will be created after K10 is deployed and the location profile is configured

# K10 Policy resources are typically managed via the dashboard initially

#

# Documentation for reference

# - Profile (location) must be created first via K10 dashboard or API

# - Policies define backup schedules, retention, and actions

#

# Example Policy structure (to be created via K10 dashboard)

#

# Cluster-wide backup policy

# - Name: cluster-backup-daily

# - Frequency: Daily at 2 AM

# - Retention: 7 daily, 4 weekly, 3 monthly

# - Actions: Snapshot, Export to NFS location

# - Resources: All namespaces with PVCs

#

# Namespace-specific policies

# - nextcloud: Daily at 3 AM, export to NFS

# - home-assistant: Daily at 3:30 AM, export to NFS

# - monitoring: Weekly on Sunday at 4 AM, export to NFS

#

# These will be configured via the K10 dashboard at backup.infra.k8s.wlkr.ch

# after initial deployment and NFS location profile setup
