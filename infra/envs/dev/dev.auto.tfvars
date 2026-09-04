# Non-secret environment values. Edit before the first apply.
alert_recipients       = ["hoangmnguyen.work@gmail.com"]
ses_sender             = "hoangmnguyen.work@gmail.com"
github_repo            = "HoangMNguyen/pricepulse"
alert_min_discount_pct = 20

# Site hostname (delegated subdomain: add the NS records from `terraform output name_servers` at the parent DNS host).
domain_name = "pricepulse.hoangmnguyen.com"
neon_org_id = "org-little-hill-22275258"
domain_attached = true
