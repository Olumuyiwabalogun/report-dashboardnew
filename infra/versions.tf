terraform {
  required_version = ">= 1.6"

  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }

  # Values come from envs/<env>.backend.hcl so dev and prod use separate state.
  backend "s3" {}
}

provider "aws" {
  region = var.region
}
