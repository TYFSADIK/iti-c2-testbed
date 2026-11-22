# 5. Failing back to site A (manual, on purpose)

After a failover, site B is the primary and site A's database is a stale,
fenced copy. The controller never fails back by itself. When site A is healthy:

1. Make site A a replica of site B. Delete A's database cluster (its data is
   older than B's) and recreate it as a replica of B:
   ```bash
   kubectl --kubeconfig secrets/kube/site-a.yaml -n c2 delete cluster c2db
   # copy B's c2db-ca and c2db-replication secrets to A as site-b-ca / site-b-replication,
   # then apply k8s/site-standby/cluster.yaml with site-b names and SITE_A_DB_HOST set to B's address
   ```
2. Wait until A has caught up (`kubectl cnpg status c2db` on A shows streaming).
3. Planned switchover in a quiet minute: stop writes (scale the C2 Board at B to 0),
   promote A, demote B to a replica of A, set `ACTIVE=a` and restart the controller,
   scale the C2 Board back up.

Why manual: an automatic failback on a flapping link would move the service
back and forth and lose writes each time. A person decides when site A is
trustworthy again.
