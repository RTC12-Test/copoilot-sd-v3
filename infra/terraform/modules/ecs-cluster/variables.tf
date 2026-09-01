variable "app_name" {
  description = "Application name"
  type        = string
  default     = ""
}
variable "org_name" {
  description = "Organization name"
  type        = string
  default     = ""
}
variable "env" {
  description = "Environment"
  type        = string
  default     = ""
}
variable "enable_container_insights_monitoring" {
  description = "The value to assign to the setting. Valid values are enabled and disabled"
  type        = string
  default     = "enabled"
}
variable "default_tags" {
  description = "Default tags for all resources"
  type        = map(string)
  default     = {}
}
variable "map_migrated_tag" {
  description = "Workloads moving to AWS should have this tag"
  type        = string
  default     = ""
}
variable "aws_cloudwatch_container_insights" {
  description = "Name of the setting to manage. Valid values: containerInsight"
  type        = string
  default     = "containerInsights"
}

