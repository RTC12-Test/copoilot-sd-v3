variable "config_path" {
  description = "Path to configs"
  type        = string
  default     = ""
}
variable "environment" {
  description = "Environment"
  type        = string
  default     = ""
}
variable "default_tags" {
  description = "Default tags for all resources"
  type        = map(string)
  default = {
    "resourcetaggedby" = "terraform"
    "createdby"        = "terraform"
    "owner"            = "devops"
  }
}
variable "division" {
  description = "Tag for division"
  type        = string
  default     = ""
}
variable "technicalContact" {
  description = "Tag for technicalContact"
  type        = string
  default     = ""
}
variable "department" {
  description = "Tag for department"
  type        = string
  default     = ""
}
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
