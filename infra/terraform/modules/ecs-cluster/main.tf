# Resource to create aws ecs cluster
resource "aws_ecs_cluster" "ecs_cluster" {
  name = "${var.org_name}-${var.app_name}-${var.env}-ecs-cluster"
  setting {
    name  = var.aws_cloudwatch_container_insights
    value = var.enable_container_insights_monitoring
  }
  tags = merge(var.default_tags, {
    Name         = "${var.org_name}-${var.app_name}-${var.env}-ecs-cluster"
    map-migrated = var.map_migrated_tag
  })
}
