#!/usr/bin/env python3
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv('/home/ubuntu/crypto-scanner/.env')

sid = os.environ.get('COS_SECRET_ID', '')
skey = os.environ.get('COS_SECRET_KEY', '')
region = os.environ.get('COS_REGION', 'ap-seoul')
endpoint = os.environ.get('COS_ENDPOINT', 'cos.ap-seoul.myqcloud.com')
bucket = os.environ.get('COS_BUCKET', '')

print("=== 测试COS SDK ===")
print(f"SecretId: {sid[:10]}...")
print(f"SecretKey: {skey[:10]}...")
print(f"Region: {region}")
print(f"Endpoint: {endpoint}")
print(f"Bucket: {bucket}")

try:
    from qcloud_cos import CosConfig, CosS3Client

    print("\n创建CosConfig...")
    cos_config = CosConfig(Region=region, SecretId=sid, SecretKey=skey, Endpoint=endpoint)

    print("创建CosS3Client...")
    client = CosS3Client(cos_config)

    print("COS客户端创建成功!")

    # 测试列出对象
    print("\n测试列出对象...")
    try:
        response = client.list_objects(Bucket=bucket, MaxKeys=5)
        print(f"列出对象成功: {len(response.get('Contents', []))} 个对象")
    except Exception as e:
        print(f"列出对象失败: {e}")

except ImportError as e:
    print(f"导入COS SDK失败: {e}")
except Exception as e:
    print(f"COS客户端创建失败: {e}")