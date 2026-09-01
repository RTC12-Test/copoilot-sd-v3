# Resource to create cloudwatch log group
resource "aws_cloudwatch_log_group" "app" {
  name              = "${var.org_name}-${var.app_name}-${var.env}-app"
  retention_in_days = var.log_retention_days
  tags = merge(var.default_tags, {
    Name           = "${var.org_name}-${var.app_name}-${var.env}-log-group"
    "map-migrated" = var.map_migrated_tag
  })
}

# Resource to create cloudwatch log metric filter
resource "aws_cloudwatch_log_metric_filter" "error_count" {
  name           = "${var.org_name}-${var.app_name}-${var.env}-errors"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = var.error_pattern

  metric_transformation {
    name          = "ErrorCount"
    namespace     = var.metric_namespace
    value         = "1"
    default_value = "0"
  }
}

# Resource to create cloudwatch metric alarm
resource "aws_cloudwatch_metric_alarm" "high_error_rate" {
  alarm_name          = "${var.org_name}-${var.app_name}-${var.env}-high-error-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ErrorCount"
  namespace           = var.metric_namespace
  period              = var.alarm_period
  statistic           = "Sum"
  threshold           = var.alarm_threshold
  alarm_description   = "More than ${var.alarm_threshold} errors in a ${var.alarm_period / 60}-minute window"
  alarm_actions       = [var.alarm_sns_topic_arn]
  tags = merge(var.default_tags, {
    Name           = "${var.org_name}-${var.app_name}-${var.env}-alarm"
    "map-migrated" = var.map_migrated_tag
  })

  dimensions = {
    LogGroup = aws_cloudwatch_log_group.app.name
  }
}