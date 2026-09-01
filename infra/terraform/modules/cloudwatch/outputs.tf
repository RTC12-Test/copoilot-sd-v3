# Output for cloudwatch log group name
output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}
# Output for cloudwatch log group arn
output "log_group_arn" {
  value = aws_cloudwatch_log_group.app.arn
}
# Output for cloudwatch alarm arn
output "alarm_arn" {
  value = aws_cloudwatch_metric_alarm.high_error_rate.arn
}
