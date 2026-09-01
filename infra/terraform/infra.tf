# Calling locals of config module
locals {
  configs      = module.config.config_env # Retrieve configurations from the config module
  default_tags = module.config.default_tags
}

# Module for configuring common settings
module "config" {
  source      = "./modules/config"
  environment = var.env
}

# Module for calling ecs-cluster module
module "ecs-cluster" {
  source                               = "./modules/ecs-cluster"
  org_name                             = lookup(local.configs, "org_name")
  app_name                             = lookup(local.configs, "app_name")
  env                                  = var.env
  default_tags                         = local.default_tags
  map_migrated_tag                     = lookup(local.configs, "map_migrated_tag")
  enable_container_insights_monitoring = lookup(local.configs, "ecs_enable_container_insights")
  aws_cloudwatch_container_insights    = lookup(local.configs, "ecs_container_insights_setting")
}

# Module for creating s3 bucket
module "s3" {
  source           = "./modules/s3"
  org_name         = lookup(local.configs, "org_name")
  app_name         = lookup(local.configs, "app_name")
  env              = var.env
  service_name     = lookup(local.configs, "service_name")
  default_tags     = local.default_tags
  map_migrated_tag = lookup(local.configs, "map_migrated_tag")
  aws_s3_buckets   = lookup(local.configs, "aws_s3_buckets")
}

# Module for creating cloudwatch monitoring
module "cloudwatch" {
  source           = "./modules/cloudwatch"
  org_name         = lookup(local.configs, "org_name")
  app_name         = lookup(local.configs, "app_name")
  env              = var.env
  default_tags     = local.default_tags
  map_migrated_tag = lookup(local.configs, "map_migrated_tag")
}

