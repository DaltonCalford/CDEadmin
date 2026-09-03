# CDEadmin Kubernetes Helm chart

This chart deploys CDEadmin in Kubernetes environments, including restricted
security contexts. It supports local configuration, predefined endpoint
definitions, preferences, persistent state and standard Kubernetes ingress or
Gateway API routing.

CDEadmin is an independent hard fork of pgAdmin 4 9.17. The upstream pgAdmin
copyright and PostgreSQL Licence are retained; see the repository `NOTICE` and
`LICENSE`. This chart uses separate CDEadmin resource names and storage paths.

### Package

The chart is not approved for publication until CDEadmin's registry, signing
and release-engineering gates are complete.

`helm package .`

### Installation Example:
`helm install mycdeadmin oci://docker.io/cdeadmin/cdeadmin-helm --set ingress.enabled=true`

### Important Values
| Value | Description | Default |
| --------- | ----------- | ------- |
| `containerPort` | Internal CDEadmin Port | `5051` |
| `image.registry` | Image registry | `"docker.io"` |
| `image.repository` | Image Repository | `"cdeadmin/cdeadmin"` |
| `image.tag` | Image tag (If empty, will use .Chart.AppVersion) | `""` |
| `auth.email` | Admin Email | `"admin@cdeadmin.local"` |
| `auth.password` | Admin password (If both auth.password and auth.existingSecret are empty, the password will be randomly generated) | `""` |
| `auth.existingSecret` | Existing secret name for admin password (If both auth.password and auth.existingSecret are empty, the password will be randomly generated) | `""` |
| `extraEnvVars` | Extra environment variables | `[]` |
| `config_local.enabled` | Whether to mount config_local.py file | `false` |
| `config_local.data` | config_local.py configuration content | `""` |
| `config_local.existingSecret` | Existing secret name containing config_local.py file | `""` |
| `serverDefinitions.enabled` | Whether to mount servers.json | `false` |
| `serverDefinitions.data` | Server definitions to import | `{}` |
| `preferences.enabled` | Whether to mount preferences.json | `false` |
| `preferences.data` | Preferences to load | `{}` |
| `resources.*` | Allocated requests and limits resources | `{"requests": {...}, "limits": {...}}` |
| `persistence.enabled` | PVC resource creation | `true` |
| `persistence.existingClaim` | Provide existing PVC instead of creating one | `""` |
| `service.type` | Service type | `"ClusterIP"` |
| `service.loadBalancerIP` | Load balancer IP (Only if service.type is LoadBalancer) | `""` |
| `ingress.enabled` | Ingress resource creation | `false` |
| `ingress.hostname` | Ingress resource hostname | `"cdeadmin.local"` |
| `ingress.tlsSecret` | Ingress tls secret name | `""` |
| `httpRoute.enabled` | Gateway API HTTPRoute resource creation | `false` |
| `httpRoute.apiVersion` | HTTPRoute apiVersion (override for v1beta1 implementations) | `"gateway.networking.k8s.io/v1"` |
| `httpRoute.parentRefs` | Gateway(s) the route attaches to (required when `httpRoute.enabled` is true) | `[]` |
| `httpRoute.hostnames` | HTTPRoute hostnames (falls back to `ingress.hostname` when empty) | `[]` |
| `httpRoute.annotations` | HTTPRoute annotations | `{}` |
| `httpRoute.rules` | Custom routing rules (defaults to a single `/` PathPrefix rule to the service) | `[]` |
| `strategy.type` | Deployment strategy type (RollingUpdate or Recreate) | Kubernetes default (RollingUpdate) |
| `strategy.rollingUpdate.maxSurge` | Maximum number of pods that can be created over the desired replicas | Kubernetes default (25%) |
| `strategy.rollingUpdate.maxUnavailable` | Maximum number of pods that can be unavailable during the update | Kubernetes default (25%) |
