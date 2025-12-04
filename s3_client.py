import boto3
# ✅ AWS S3 클라이언트 (항상 키 포함)
s3 = boto3.client("s3",
    aws_access_key_id="AKIAQOAKFOWUA3FXVWU5",
    aws_secret_access_key="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",
    region_name="us-east-1"
)
bucket_name = "capstone-emergency-vehicle-evasion"
