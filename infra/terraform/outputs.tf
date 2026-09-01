output "s3_bucket_name" {
  value = module.s3.s3_bucket_name
}
output "log_group_name" {
  value = module.cloudwatch.log_group_name
}
