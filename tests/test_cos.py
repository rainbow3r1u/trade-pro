#!/usr/bin/env python3
import os
import sys

print("=== 测试COS环境变量 ===")

# 方法1: 直接检查os.environ
print("1. 直接检查os.environ:")
print(f"   COS_SECRET_ID: {os.environ.get('COS_SECRET_ID', 'NOT FOUND')}")
print(f"   COS_SECRET_KEY: {os.environ.get('COS_SECRET_KEY', 'NOT FOUND')}")
print(f"   COS_BUCKET: {os.environ.get('COS_BUCKET', 'NOT FOUND')}")

# 方法2: 加载.env文件
from dotenv import load_dotenv
load_dotenv('/home/ubuntu/crypto-scanner/.env')

print("\n2. 加载.env后检查os.environ:")
print(f"   COS_SECRET_ID: {os.environ.get('COS_SECRET_ID', 'NOT FOUND')}")
print(f"   COS_SECRET_KEY: {os.environ.get('COS_SECRET_KEY', 'NOT FOUND')}")
print(f"   COS_BUCKET: {os.environ.get('COS_BUCKET', 'NOT FOUND')}")

# 方法3: 模拟market_monitor_app.py的代码
COS_SECRET_ID = os.environ.get('COS_SECRET_ID', '')
COS_SECRET_KEY = os.environ.get('COS_SECRET_KEY', '')
COS_BUCKET = os.environ.get('COS_BUCKET', '')

print("\n3. 模拟market_monitor_app.py变量:")
print(f"   COS_SECRET_ID变量: '{COS_SECRET_ID}'")
print(f"   COS_SECRET_KEY变量: '{COS_SECRET_KEY}'")
print(f"   COS_BUCKET变量: '{COS_BUCKET}'")

# 测试get_cos_client逻辑
print("\n4. 测试get_cos_client逻辑:")
current_sid = os.environ.get('COS_SECRET_ID', '')
print(f"   current_sid (os.environ): '{current_sid}'")
print(f"   COS_SECRET_ID (模块变量): '{COS_SECRET_ID}'")
print(f"   两者是否相等: {current_sid == COS_SECRET_ID}")
print(f"   current_sid是否非空: {bool(current_sid)}")
print(f"   COS_SECRET_ID != current_sid and current_sid: {COS_SECRET_ID != current_sid and bool(current_sid)}")