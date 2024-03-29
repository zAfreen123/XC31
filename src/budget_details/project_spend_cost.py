# Copyright (c) 2023, Xgrid Inc, https://xgrid.co

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#        http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import botocore
import logging
import os
from datetime import timedelta

import boto3
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
import json

try:
    ec2_client = boto3.client("ec2")
except Exception as e:
    logging.error("Error creating boto3 client for ec2: " + str(e))
try:
    s3 = boto3.client("s3")
except Exception as e:
    logging.error("Error creating boto3 client for s3: " + str(e))
try:
    ce_client = boto3.client("ce")
except Exception as e:
    logging.error("Error creating boto3 client for ce: " + str(e))
try:
    lambda_client = boto3.client("lambda")
except Exception as e:
    logging.error("Error creating boto3 client for lambda: " + str(e))


cost_by_days = 30
end_date = str(datetime.datetime.now().date())
start_date = str(datetime.datetime.now().date() - timedelta(days=cost_by_days))


def cost_of_project(ce_client, start_date, end_date):
    """
    Calculates the cost of a project for a given time period.

    Args:
        ce_client (boto3.client): The AWS Cost Explorer client.
        start_date (str): The start date of the time period in 'YYYY-MM-DD' format.
        end_date (str): The end date of the time period in 'YYYY-MM-DD' format.

    Returns:
        dict: The cost of the project, grouped by project tag.
    """
    try:
        response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[
                {"Type": "TAG", "Key": "Project"},
            ],
        )
        return response
    except Exception as e:
        print(f"Error getting cost of project: {e}")
        return None


def lambda_handler(event, context):
    """
    The main function that is executed when the AWS Lambda function is triggered.

    Returns:
        str: A message indicating the success or failure of the function execution.
    """
    project_cost_breakdown_lambda = os.environ["lambda_function_name"]

    try:
        registry = CollectorRegistry()
        g = Gauge(
            "Project_Spend_Cost",
            "XC3 Project Spend Cost",
            labelnames=["project_spend_project", "project_spend_cost"],
            registry=registry,
        )

        response = cost_of_project(ce_client, start_date, end_date)
        project_dict = {}

        for group in response["ResultsByTime"][0]["Groups"]:
            tag_key = group["Keys"][0]
            cost = group["Metrics"]["UnblendedCost"]["Amount"]

            print(f"{tag_key}: {cost}")
            tag_value = tag_key.split("$")[1]
            if tag_value == "":
                tag_value = "Others"
            print(tag_value)
            g.labels(tag_value, cost).set(cost)
            project_dict[tag_value] = cost

        # Convert the dictionary to JSON
        json_data = json.dumps(project_dict)

        # Upload the file to S3
        s3.put_object(
            Bucket=os.environ["bucket_name"],
            Key=os.environ["project_spend_prefix"],
            Body=json_data,
        )

        push_to_gateway(
            os.environ["prometheus_ip"], job="Project-Spend-Cost", registry=registry
        )

        # Loop through each project and invoke the project cost breakdown lambda
        for project_name in project_dict.keys():
            payload = {"project_name": project_name}
            try:
                project_cost_breakdown_response = lambda_client.invoke(
                    FunctionName=project_cost_breakdown_lambda,
                    InvocationType="Event",
                    Payload=json.dumps(payload),
                )
                # Extract the status code from the response
                status_code = project_cost_breakdown_response["StatusCode"]
                if status_code != 202:
                    # Handle unexpected status code
                    logging.error(
                        (
                            f"Unexpected status code {status_code}returned from"
                            "project_spend_cost_breakdown_lambda"
                        )
                    )
            except Exception as e:
                logging.error("Error in invoking lambda function: " + str(e))
                return {
                    "statusCode": 500,
                    "body": "Error invoking project_cost_breakdown_lambda",
                }

        return {"statusCode": 200, "body": json_data}
    except botocore.exceptions.ClientError as e:
        logging.error(f"Failed to upload file to S3: {e}")
        return {"statusCode": 500, "body": "Failed to upload file to S3."}

    except Exception as e:
        logging.error(f"Error executing lambda_handler: {e}")
        return "Failed to execute. Please check logs for more details."
