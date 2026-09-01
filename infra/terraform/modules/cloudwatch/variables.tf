variable "org_name" {
  description = "Organization name"
  type        = string
  default     = ""
}
variable "app_name" {
  description = "Application name"
  type        = string
  default     = ""
}
variable "env" {
  description = "Environment"
  type        = string
  default     = ""
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
variable "log_retention_days" {
  description = "Number of days to retain logs"
  type        = number
  default     = 30
}
variable "error_pattern" {
  description = "Pattern to match for error log entries"
  type        = string
  default     = "?ERROR ?Exception ?error"
}
variable "metric_namespace" {
  description = "CloudWatch metric namespace"
  type        = string
  default     = "Custom/Application"
}
variable "alarm_period" {
  description = "Alarm evaluation period in seconds"
  type        = number
  default     = 300
}
variable "alarm_threshold" {
  description = "Error count threshold to trigger alarm"
  type        = number
  default     = 10
}
variable "alarm_sns_topic_arn" {
  description = "ARN of the SNS topic for alarm notifications"
  type        = string
  default     = ""
}
