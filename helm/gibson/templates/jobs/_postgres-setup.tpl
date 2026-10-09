{{- /*
gibson.postgresSetup.podTemplate — the pod of every postgres-setup Job.

Each Job creates one or more owner roles and their databases on the
platform Postgres (the CNPG Cluster platform-postgres), and gives each owner
its database's public schema. An owner role logs in no more (NOLOGIN, no
password): the OpenBao database engine issues the login roles, members of the
owner that set role to it at each login (ADR-0171, row
postgres-role-passwords). It is a plain Postgres client:

  - It connects to the platform-postgres-rw Service as the CNPG superuser,
    with the password CNPG writes to platform-postgres-superuser
    (enableSuperuserAccess: true), over TLS (sslmode=require, the mode every
    other platform client uses).
  - The kubelet hands it the superuser password through secretKeyRef, so the
    pod waits in CreateContainerConfigError until CNPG has written the
    Secret, and the Job never reads a Secret through the API.
  - Its ServiceAccount holds no RBAC and mounts no token.

It used to reach the primary with `kubectl exec` into the postgres
container. That needed `create pods/exec` in the namespace, and a pods/exec
grant cannot be limited to the database pods: the CNPG primary is elected,
so its pod name is not known in advance. Exec into any pod in the namespace
is the same as becoming that workload, the ones that hold the Zitadel owner
credentials included (scripts/check-owner-credential-readers.py).

Every step is idempotent, so a re-run (an upgrade, a retry) converges:

  1. create the role if it is missing
  2. set its options (always, so a changed option lands on the next run),
     and remove any password it had: an owner logs in no more
  3. create the database with the role as owner if it is missing, else make
     the role its owner
  4. grant the role every privilege on the database
  5. make the role the owner of the database's public schema (PostgreSQL 15
     and later no longer grant CREATE on it to everyone)

The SQL takes every name through psql variables and format(%I, %L), so no
value is ever spliced into SQL text by the shell.

Input: (dict "root" $ "entries" (list (dict "role" "db" "opts")) "extra" "<bash>")
*/ -}}
{{- define "gibson.postgresSetup.podTemplate" -}}
{{- $root := .root }}
{{- $img := $root.Values.postgresSetup.image -}}
metadata:
  labels:
    app.kubernetes.io/component: postgres-setup
    # The Postgres data store admits its clients on :5432 (templates/network-policies.yaml).
    {{- include "gibson.netLabels" (dict "role" "platform" "clients" (list "postgres")) | nindent 4 }}
spec:
  restartPolicy: OnFailure
  serviceAccountName: postgres-setup
  automountServiceAccountToken: false
  # Secure pod (ADR-0165, charts#394): non-root, the RuntimeDefault seccomp
  # profile, a read-only root filesystem and no capabilities. The script
  # writes only to /tmp, an emptyDir.
  securityContext:
    runAsNonRoot: true
    runAsUser: 1001
    runAsGroup: 1001
    seccompProfile:
      type: RuntimeDefault
  volumes:
  - name: tmp
    emptyDir: {}
  containers:
  - name: setup
    image: {{ printf "%s:%s" $img.repository $img.tag | quote }}
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop: ["ALL"]
    volumeMounts:
    - name: tmp
      mountPath: /tmp
    env:
    - name: PGHOST
      value: platform-postgres-rw.{{ $root.Release.Namespace }}.svc
    - name: PGPORT
      value: "5432"
    - name: PGSSLMODE
      value: require
    - name: PGCONNECT_TIMEOUT
      value: "5"
    - name: PGUSER
      valueFrom:
        secretKeyRef:
          name: platform-postgres-superuser
          key: username
    - name: PGPASSWORD
      valueFrom:
        secretKeyRef:
          name: platform-postgres-superuser
          key: password
    command: [bash, -ec]
    args:
    - |
      set -euo pipefail
      # Bitnami puts the client under /opt/bitnami/postgresql/bin.
      export PATH="/opt/bitnami/postgresql/bin:$PATH"
      echo "[postgres-setup] waiting for platform-postgres-rw to accept connections..."
      i=0
      until pg_isready -q -d postgres; do
        i=$((i + 1))
        if [ "$i" -ge 90 ]; then
          echo "[postgres-setup] ERROR: platform-postgres-rw did not accept connections in 180s; the Job retries"
          exit 1
        fi
        sleep 2
      done
      PSQL=(psql -X -q -v ON_ERROR_STOP=1)

      # setup <owner role> <database> <role options>
      setup() {
        echo "[$1] owner role, database and schema..."
        "${PSQL[@]}" -d postgres -v role="$1" -v db="$2" -v opts="$3" <<'SQL'
      SELECT format('CREATE ROLE %I', :'role')
        WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = :'role') \gexec
      SELECT format('ALTER ROLE %I WITH %s PASSWORD NULL', :'role', :'opts') \gexec
      SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
        WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_database WHERE datname = :'db') \gexec
      SELECT format('ALTER DATABASE %I OWNER TO %I', :'db', :'role') \gexec
      SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'db', :'role') \gexec
      \connect :db
      SELECT format('ALTER SCHEMA public OWNER TO %I', :'role') \gexec
      SELECT format('GRANT ALL ON SCHEMA public TO %I', :'role') \gexec
      SQL
        echo "[$1] setup complete"
      }

      {{- range .entries }}
      setup {{ .role | quote }} {{ .db | quote }} {{ .opts | quote }}
      {{- end }}
      {{- with .extra }}

      {{- . | nindent 6 }}
      {{- end }}
      echo "[postgres-setup] ALL_ROLES_SETUP_OK"
{{- end }}
