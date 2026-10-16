{{- define "app.name" -}}{{ .Release.Name | trunc 50 | trimSuffix "-" }}{{- end -}}
{{- define "app.labels" -}}
app.kubernetes.io/name: {{ include "app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: agent-factory
{{- end -}}
{{- define "app.dbSecret" -}}{{ include "app.name" . }}-db{{- end -}}
