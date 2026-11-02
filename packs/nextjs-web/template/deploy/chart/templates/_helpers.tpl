{{- define "app.name" -}}{{ .Release.Name | trunc 50 | trimSuffix "-" }}{{- end -}}
{{- define "app.labels" -}}
app.kubernetes.io/name: {{ include "app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: agent-factory
{{- end -}}
{{- define "app.dbSecret" -}}{{ include "app.name" . }}-db{{- end -}}
{{- define "app.adminSecret" -}}{{ include "app.name" . }}-admin{{- end -}}

{{- /*
The deploy strategy (ADR-0046 slice 4), set per product by the factory's Deploy station:
in-place runs one Deployment (the blue colour) and updates it rolling; blue-green runs blue and
green side by side on the release's one database, the Service sends users to blueGreen.live and a
preview Service and host reach the other colour, which is tested before the switch.
*/ -}}
{{- define "app.blueGreen" -}}{{- if eq (.Values.strategy | default "in-place") "blue-green" -}}true{{- end -}}{{- end -}}
{{- define "app.colors" -}}{{- if include "app.blueGreen" . -}}blue green{{- else -}}blue{{- end -}}{{- end -}}
{{- define "app.live" -}}{{- if include "app.blueGreen" . -}}{{ .Values.blueGreen.live }}{{- else -}}blue{{- end -}}{{- end -}}
{{- define "app.idle" -}}{{- if eq (include "app.live" .) "green" -}}blue{{- else -}}green{{- end -}}{{- end -}}
{{- /* blue keeps the name and component the app always had; green gets its own */ -}}
{{- define "app.colorName" -}}{{ include "app.name" .root }}{{ if eq .color "green" }}-green{{ end }}{{- end -}}
{{- define "app.colorComponent" -}}{{ .component }}{{ if eq .color "green" }}-green{{ end }}{{- end -}}
{{- /* repository:tag a colour runs, or nothing: that colour isn't deployed */ -}}
{{- define "app.colorImage" -}}
{{- if include "app.blueGreen" .root -}}
{{- $img := index .root.Values.blueGreen .color | default dict -}}
{{- if $img.repository -}}{{ $img.repository }}:{{ $img.tag }}{{- end -}}
{{- else if eq .color "blue" -}}
{{ .root.Values.image.repository }}:{{ .root.Values.image.tag }}
{{- end -}}
{{- end -}}
{{- define "app.checkStrategy" -}}
{{- $s := .Values.strategy | default "in-place" -}}
{{- if not (has $s (list "in-place" "blue-green")) -}}{{ fail (printf "strategy must be in-place or blue-green, not %q" $s) }}{{- end -}}
{{- if include "app.blueGreen" . -}}
{{- if not (has .Values.blueGreen.live (list "blue" "green")) -}}{{ fail (printf "blueGreen.live must be blue or green, not %q" .Values.blueGreen.live) }}{{- end -}}
{{- if not (include "app.colorImage" (dict "root" . "color" (include "app.live" .))) -}}{{ fail (printf "blueGreen.%s has no image: users would reach nothing" (include "app.live" .)) }}{{- end -}}
{{- end -}}
{{- end -}}
