# Calls config path
locals {
  config_path = var.config_path != "" ? var.config_path : "${path.root}/config_env"
  environment = var.environment != "" ? var.environment : terraform.workspace

  default_tags = merge(var.default_tags, {
    "environment"      = local.environment
    "org"              = var.org_name
    "application"      = var.app_name
    "division"         = var.division
    "technicalContact" = var.technicalContact
    "tier"             = local.environment
    "department"       = var.department
  })

  # Decode each file, but only if it's not empty and is a valid YAML document
  files_env   = toset([for f in fileset("${local.config_path}/${local.environment}", "*.yaml") : f if substr(f, -9, -1) != ".enc.yaml"])
  configs_env = toset([for f in local.files_env : try(yamldecode(file("${local.config_path}/${local.environment}/${f}")))])
  configs     = merge(local.configs_env...)
}
