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
variable "service_name" {
  description = "Service name"
  type        = string
  default     = ""
}
variable "map_migrated_tag" {
  description = "Workloads moving to AWS should have this tag"
  type        = string
  default     = ""
}
variable "default_tags" {
  description = "Default tags for all resources"
  type        = map(string)
  default     = {}
}
variable "aws_s3_buckets" {
  type    = map(any)
  default = {}
}
