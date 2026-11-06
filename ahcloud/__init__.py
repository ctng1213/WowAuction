"""The cloud job: the hourly collector and the analyzer over Blizzard's Forever auction API (SPEC §2.4).

Standard library plus boto3 (in the Lambda runtime), and boto3 only in ahcloud/aws.py; everything else takes
injected stores, transports and clocks, so it runs the same on a laptop as in Lambda (AR9).
"""
