# GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
# content-sha256: f2467529652cb7071a25477a2c7d9b9dda19553219f8bec18c8ef95afc3d9872
connection: "spb_bigquery"

include: "/views/*.view.lkml"
include: "/refinements/*.lkml"
include: "/dashboards/*.dashboard.lookml"

named_value_format: eur_0 {
  value_format: "\"€\"#,##0"
}

named_value_format: bps_1 {
  value_format: "0.0\" bps\""
}

named_value_format: per_mille_2 {
  value_format: "0.00\" ‰\""
}

named_value_format: eur_2 {
  value_format: "\"€\"#,##0.00"
}

explore: ewi_components {
  description: "Synthetic data — fictional entity."
}

explore: ewi {
  description: "Synthetic data — fictional entity."
}

explore: dq_status {
  description: "Synthetic data — fictional entity."
}

explore: bcbs239 {
  description: "Synthetic data — fictional entity."
}

explore: timeliness {
  description: "Synthetic data — fictional entity."
}

explore: metric_catalog {
  description: "Synthetic data — fictional entity."
}

explore: requests {
  description: "Synthetic data — fictional entity."
  join: gate_decisions {
    type: left_outer
    relationship: one_to_many
    sql_on: ${requests.request_id} = ${gate_decisions.request_id} ;;
  }
}
