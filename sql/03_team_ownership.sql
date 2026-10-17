-- ============================================================================
-- 03_team_ownership.sql
-- Fictional organization mapping (dataset, component) -> owning team.
-- Components not listed here surface as 'Unassigned' in the summary tools.
-- ============================================================================

DROP TABLE IF EXISTS team_ownership;

CREATE TABLE team_ownership (
    dataset          VARCHAR,
    component        VARCHAR,
    team_name        VARCHAR,
    team_lead_slack  VARCHAR,
    escalation_tier  VARCHAR,
    component_scope  VARCHAR,
    PRIMARY KEY (dataset, component)
);

INSERT INTO team_ownership (dataset, component, team_name, team_lead_slack, escalation_tier, component_scope) VALUES
    -- BGL supercomputer infrastructure
    ('BGL', 'KERNEL',    'Core Systems & OS Team',         '#team-core-os',       'Tier-1-Critical', 'Compute-node kernel: CPU, cache parity, TLB, memory controller'),
    ('BGL', 'APP',       'Compute Runtime Team',           '#team-runtime',       'Tier-2-High',     'User job runtime and ciod I/O control streams'),
    ('BGL', 'MMCS',      'Cluster Control Plane Team',     '#team-control-plane', 'Tier-1-Critical', 'Midplane Management Control System, block boot'),
    ('BGL', 'BGLMASTER', 'Cluster Control Plane Team',     '#team-control-plane', 'Tier-1-Critical', 'Master control daemon'),
    ('BGL', 'LINKCARD',  'High-Speed Interconnect Team',   '#team-networking',    'Tier-1-Critical', 'Link cards and midplane interconnect'),
    ('BGL', 'DISCOVERY', 'Hardware Topology Team',         '#team-hardware',      'Tier-2-High',     'Node/service card discovery, power and environmental sensors'),
    ('BGL', 'HARDWARE',  'Hardware Topology Team',         '#team-hardware',      'Tier-2-High',     'Physical hardware faults reported by service cards'),
    ('BGL', 'CMCS',      'Chassis Management Team',        '#team-hardware',      'Tier-2-High',     'Card Management Control System'),
    ('BGL', 'SERV_NET',  'High-Speed Interconnect Team',   '#team-networking',    'Tier-2-High',     'Service network'),
    ('BGL', 'MONITOR',   'Observability & Telemetry Team', '#team-observability', 'Tier-3-Standard', 'Environmental monitoring daemons'),
    -- OpenStack cloud platform
    ('OpenStack', 'nova-api',       'Cloud API Gateway Team',      '#team-cloud-api', 'Tier-1-Critical', 'OSAPI and metadata WSGI endpoints'),
    ('OpenStack', 'nova-compute',   'Hypervisor & Compute Team',   '#team-hypervisor', 'Tier-1-Critical', 'libvirt instance lifecycle, image cache, resource claims'),
    ('OpenStack', 'nova-scheduler', 'Placement & Scheduling Team', '#team-scheduler', 'Tier-2-High',     'Host filtering and placement');
