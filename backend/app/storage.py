"""Private S3-compatible image storage with bounded raw-input retention."""
import os
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit
from uuid import uuid4

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError
from botocore.config import Config


class StorageError(Exception):
    pass


class ObjectStorage:
    def __init__(self, endpoint=None, bucket=None, access_key=None, secret_key=None):
        self.bucket = bucket or os.environ['S3_BUCKET']
        options = dict(aws_access_key_id=access_key or os.environ['S3_ACCESS_KEY_ID'],
                       aws_secret_access_key=secret_key or os.environ['S3_SECRET_ACCESS_KEY'], region_name='us-east-1',
                       config=Config(signature_version='s3v4', connect_timeout=3, read_timeout=5, retries={'max_attempts': 1}))
        self.client = boto3.client('s3', endpoint_url=endpoint or os.getenv('S3_ENDPOINT_URL'), **options)
        self.signer = boto3.client('s3', endpoint_url=os.getenv('S3_PUBLIC_ENDPOINT_URL') or endpoint or os.getenv('S3_ENDPOINT_URL'), **options)

    def _verify_r2_private(self, account, jurisdiction=None):
        """R2 privacy uses domain settings, not unsupported S3 bucket policies."""
        token = os.getenv('CLOUDFLARE_API_TOKEN')
        if not token:
            raise StorageError('R2 privacy verification requires CLOUDFLARE_API_TOKEN')
        headers = {'Authorization': f'Bearer {token}'}
        if jurisdiction:
            headers['cf-r2-jurisdiction'] = jurisdiction
        base = f'https://api.cloudflare.com/client/v4/accounts/{account}/r2/buckets/{quote(self.bucket, safe="")}/domains'
        settings = {}
        try:
            with httpx.Client(timeout=5, follow_redirects=False) as client:
                for kind in ('managed', 'custom'):
                    response = client.get(f'{base}/{kind}', headers=headers)
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or payload.get('success') is not True or not isinstance(payload.get('result'), dict):
                        raise StorageError('R2 privacy settings could not be verified')
                    settings[kind] = payload['result']
        except (httpx.HTTPError, ValueError) as exc:
            raise StorageError('R2 privacy settings could not be verified') from exc
        domains = settings['custom'].get('domains')
        if settings['managed'].get('enabled') is not False or not isinstance(domains, list) or any(
                not isinstance(domain, dict) or domain.get('enabled') is not False for domain in domains):
            raise StorageError('Public or unverified R2 bucket access is not permitted')

    def put(self, data, content_type):
        key = str(uuid4())
        try:
            try:
                self.client.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                if exc.response['Error']['Code'] not in ('404', 'NoSuchBucket'):
                    raise
                self.client.create_bucket(Bucket=self.bucket)
            endpoint = urlsplit(self.client.meta.endpoint_url)
            r2 = re.fullmatch(r'([a-f0-9]{32})(?:\.(eu|fedramp))?\.r2\.cloudflarestorage\.com', endpoint.hostname or '')
            if r2:
                if endpoint.scheme != 'https':
                    raise StorageError('R2 requires HTTPS')
                self._verify_r2_private(*r2.groups())
            else:
                try:
                    policy = json.loads(self.client.get_bucket_policy(Bucket=self.bucket)['Policy'])
                    for statement in policy.get('Statement', []):
                        if statement.get('Effect') == 'Allow' and '*' in str(statement.get('Principal', '')):
                            raise StorageError('Public bucket policies are not permitted')
                except ClientError as exc:
                    if exc.response['Error']['Code'] not in ('NoSuchBucketPolicy', 'NoSuchPolicy'):
                        raise
            # No public ACL or policy. Only pixel-reencoded bytes reach this method.
            expires = datetime.now(timezone.utc) + timedelta(days=30)
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                Metadata={'bodega-raw': '1', 'expires-at': expires.isoformat()})
            return key
        except (BotoCoreError, ClientError) as exc:
            raise StorageError('Image storage unavailable') from exc

    def signed_url(self, key):
        return self.signer.generate_presigned_url('get_object', Params={'Bucket': self.bucket, 'Key': key}, ExpiresIn=300)

    def delete(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError('Image cleanup unavailable') from exc

    def expired_keys(self, now=None):
        """Discover only expired objects durably marked by this application."""
        now = now or datetime.now(timezone.utc)
        try:
            for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket):
                for obj in page.get('Contents', []):
                    meta = self.client.head_object(Bucket=self.bucket, Key=obj['Key']).get('Metadata', {})
                    if meta.get('bodega-raw') != '1':
                        continue
                    try:
                        expires = datetime.fromisoformat(meta['expires-at'])
                        expired = expires <= now
                    except (KeyError, ValueError, TypeError):
                        continue
                    if expired:
                        yield obj['Key']
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'NoSuchBucket':
                raise StorageError('Image cleanup unavailable') from exc
        except BotoCoreError as exc:
            raise StorageError('Image cleanup unavailable') from exc

    def cleanup(self, now=None):
        """Only delete objects explicitly marked by this application and expired."""
        deleted = 0
        for key in self.expired_keys(now):
            self.delete(key)
            deleted += 1
        return deleted
