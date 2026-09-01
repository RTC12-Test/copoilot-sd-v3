# Output for s3 bucket
output "s3_bucket_name" {
  value = { for k, v in aws_s3_bucket.s3_bucket : k => v.bucket }
}
# Output of s3 bucket id
output "aws_s3_bucket_id" {
  value = { for k, v in aws_s3_bucket.s3_bucket : k => v.id }
}
# Output of s3 bucket arn
output "aws_s3_bucket_arn" {
  description = "The s3 bucket arn"
  value       = { for k, v in aws_s3_bucket.s3_bucket : k => v.arn }
}
