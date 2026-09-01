# Output for ecs cluster id
output "aws_ecs_cluster_id" {
  value = aws_ecs_cluster.ecs_cluster.id
}
# Output for ecs cluster name
output "aws_ecs_cluster_name" {
  value = aws_ecs_cluster.ecs_cluster.name
}
