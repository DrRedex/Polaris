-- Database-enforced least privilege using grants and aggregate views.
-- SereneDB 26.10.1 exposes PostgreSQL-compatible roles/grants; native row policies
-- are not assumed. Run as postgres after loading the corpus. Set credentials outside SQL.
CREATE OR REPLACE VIEW v_team_lead_patterns AS
SELECT l.dataset, l.component, l.severity,
       date_trunc('hour', l.event_time) + CASE WHEN EXTRACT(MINUTE FROM l.event_time) >= 30
            THEN INTERVAL '30 minutes' ELSE INTERVAL '0 minutes' END AS window_start,
       COUNT(*) AS log_lines, COUNT(*) FILTER (WHERE l.is_anomaly) AS anomaly_lines,
       MIN(l.event_time) AS first_seen, MAX(l.event_time) AS last_seen,
       o.team_name, o.escalation_tier
FROM (
    SELECT 'BGL' AS dataset, component, severity, event_time, is_anomaly FROM bgl_logs
    UNION ALL
    SELECT 'OpenStack', component, severity, event_time, is_anomaly FROM openstack_logs
) l LEFT JOIN team_ownership o ON o.dataset = l.dataset AND o.component = l.component
GROUP BY l.dataset, l.component, l.severity, window_start, o.team_name, o.escalation_tier;

CREATE OR REPLACE VIEW v_manager_summary AS
SELECT l.dataset, l.component, l.severity, COUNT(*) AS frequency,
       COUNT(DISTINCT l.event_time::DATE) AS persistence_days,
       AVG(l.latency_sec) AS avg_latency_sec,
       quantile_cont(l.latency_sec, 0.95) AS p95_latency_sec,
       o.team_name, o.escalation_tier
FROM (
    SELECT 'BGL' AS dataset, component, severity, event_time, NULL::DOUBLE AS latency_sec FROM bgl_logs
    UNION ALL
    SELECT 'OpenStack', component, severity, event_time, latency_sec FROM openstack_logs
) l LEFT JOIN team_ownership o ON o.dataset = l.dataset AND o.component = l.component
GROUP BY l.dataset, l.component, l.severity, o.team_name, o.escalation_tier;

-- Provision role passwords separately; these statements grant no administrative rights.
-- SereneDB GRANT/REVOKE take one object and one grantee per statement.
GRANT USAGE ON SCHEMA public TO engineer_role;
GRANT USAGE ON SCHEMA public TO team_lead_role;
GRANT USAGE ON SCHEMA public TO manager_role;
REVOKE ALL ON bgl_logs FROM PUBLIC;
REVOKE ALL ON openstack_logs FROM PUBLIC;
REVOKE ALL ON team_ownership FROM PUBLIC;
REVOKE ALL ON v_team_lead_patterns FROM PUBLIC;
REVOKE ALL ON v_manager_summary FROM PUBLIC;
GRANT SELECT ON bgl_logs TO engineer_role;
GRANT SELECT ON openstack_logs TO engineer_role;
GRANT SELECT ON team_ownership TO engineer_role;
GRANT SELECT ON v_team_lead_patterns TO team_lead_role;
GRANT SELECT ON team_ownership TO team_lead_role;
GRANT SELECT ON v_manager_summary TO manager_role;
REVOKE ALL ON bgl_logs FROM team_lead_role;
REVOKE ALL ON openstack_logs FROM team_lead_role;
REVOKE ALL ON bgl_logs FROM manager_role;
REVOKE ALL ON openstack_logs FROM manager_role;
REVOKE ALL ON team_ownership FROM manager_role;
