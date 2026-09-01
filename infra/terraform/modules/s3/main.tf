# Resource to create s3 bucket
resource "aws_s3_bucket" "s3_bucket" {
  for_each = { for i in var.aws_s3_buckets : i.s3service_name => i }
  bucket   = "${var.org_name}-${var.app_name}-${var.env}-${each.value.s3service_name}-bucket"
  tags = merge(var.default_tags, {
    Name         = "${var.org_name}-${var.app_name}-${var.env}-${each.value.s3service_name}-bucket"
    "map-migrated" = var.map_migrated_tag
  })
}

resource "aws_s3_bucket_versioning" "s3_versioning" {
  for_each = { for i in var.aws_s3_buckets : i.s3service_name => i }
  bucket   = aws_s3_bucket.s3_bucket[each.key].id
  versioning_configuration {
    status = try(each.value.versioning_status, "Enabled")
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "s3_encryption" {
  for_each = { for i in var.aws_s3_buckets : i.s3service_name => i }
  bucket   = aws_s3_bucket.s3_bucket[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = try(each.value.sse_algorithm, "AES256")
    }
  }
}