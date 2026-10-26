"""Build the reviewable v1.2 customer stack. This command deploys nothing."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    t = json.loads((ROOT / "deploy/amazon-quick/customer-access.template.json").read_text())
    t["Description"] = "BackBond synthetic Path C pilot: three-user expiring role, private log group, optional filtered Lambda subscription or vended-log delivery. No Quick users or sources are created."
    p, resources = t["Parameters"], t["Resources"]
    p["PilotLogGroup"]["Description"] = "New dedicated pilot group. Never use an existing shared production group."
    p["PilotLogGroup"]["Default"] = "/aws/quick/backbond-pilot-logs"
    p.update({
        "AgentId": {"Type": "String", "AllowedPattern": "^[A-Za-z0-9_.+-]{1,256}$"},
        "LoggingMode": {"Type": "String", "Default": "forward_existing", "AllowedValues": ["forward_existing", "new_delivery", "existing_delivery_source"],
                        "Description": "Direct delivery is account-wide. Use only in a dedicated synthetic sandbox; production requires the three-user forwarder."},
        "SharedLogGroupName": {"Type": "String", "Default": "", "AllowedPattern": "^[A-Za-z0-9_./#-]{0,512}$"},
        "DeliverySourceName": {"Type": "String", "Default": "backbond-quick-pilot-chat", "AllowedPattern": "^[A-Za-z0-9_-]{1,60}$"},
        "RetentionDays": {"Type": "Number", "Default": 30, "AllowedValues": [1, 3, 5, 7, 14, 30]},
        "ForwarderReservedConcurrency": {"Type": "Number", "Default": 2, "AllowedValues": [0, 1, 2],
            "Description": "Use 0 to omit a reservation when the account cannot retain AWS's minimum unreserved pool. Review the account concurrency quota before deployment."},
    })
    t["Conditions"] = {
        "ForwardExisting": {"Fn::Equals": [{"Ref": "LoggingMode"}, "forward_existing"]},
        "NewDeliverySource": {"Fn::Equals": [{"Ref": "LoggingMode"}, "new_delivery"]},
        "UseDelivery": {"Fn::Not": [{"Fn::Equals": [{"Ref": "LoggingMode"}, "forward_existing"]}]},
        "ReserveForwarderConcurrency": {"Fn::Not": [{"Fn::Equals": [{"Ref": "ForwarderReservedConcurrency"}, 0]}]},
    }
    t["Rules"] = {"SeparateGroups": {"RuleCondition": {"Fn::Equals": [{"Ref": "LoggingMode"}, "forward_existing"]},
        "Assertions": [{"Assert": {"Fn::Not": [{"Fn::Equals": [{"Ref": "SharedLogGroupName"}, ""]}]}, "AssertDescription": "Existing shared group is required for the forwarder."},
                       {"Assert": {"Fn::Not": [{"Fn::Equals": [{"Ref": "SharedLogGroupName"}, {"Ref": "PilotLogGroup"}]}]}, "AssertDescription": "Source and destination must differ to prevent recursion."}]}}
    group_arn = {"Fn::Sub": "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:${PilotLogGroup}"}
    group_streams = {"Fn::Sub": "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:${PilotLogGroup}:*"}
    source_arn = {"Fn::Sub": "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:${SharedLogGroupName}:*"}
    resources["PilotLogs"] = {"Type": "AWS::Logs::LogGroup", "Properties": {
        "LogGroupName": {"Ref": "PilotLogGroup"}, "RetentionInDays": {"Ref": "RetentionDays"},
        "Tags": [{"Key": "BackBondScope", "Value": "synthetic-only"}]}}
    resources["PilotStream"] = {"Type": "AWS::Logs::LogStream", "Properties": {
        "LogGroupName": {"Ref": "PilotLogs"}, "LogStreamName": "pilot-chat"}}
    resources["PilotRuntimeRole"]["DependsOn"] = "PilotLogs"
    resources["ForwarderRole"] = {"Type": "AWS::IAM::Role", "Condition": "ForwardExisting", "Properties": {
        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
        "Policies": [{"PolicyName": "WriteOnlySyntheticPilotStream", "PolicyDocument": {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Action": "logs:PutLogEvents", "Resource": group_streams,
            "Condition": {"DateLessThan": {"aws:CurrentTime": {"Ref": "PilotExpiresAt"}}}}]}}]}}
    users = [{"Ref": key} for key in ("EmployeeUserArn", "SalesManagerUserArn", "HrUserArn")]
    resources["PilotLogForwarder"] = {"Type": "AWS::Lambda::Function", "Condition": "ForwardExisting", "DependsOn": "PilotStream", "Properties": {
        "Runtime": "python3.12", "Handler": "index.handler", "Role": {"Fn::GetAtt": ["ForwarderRole", "Arn"]},
        "Timeout": 30, "MemorySize": 128, "ReservedConcurrentExecutions": {"Fn::If": ["ReserveForwarderConcurrency", {"Ref": "ForwarderReservedConcurrency"}, {"Ref": "AWS::NoValue"}]},
        "Code": {"ZipFile": (ROOT / "deploy/amazon-quick/log_forwarder.py").read_text(encoding="utf-8")},
        "Environment": {"Variables": {"ACCOUNT_ID": {"Ref": "AWS::AccountId"}, "SOURCE_GROUP": {"Ref": "SharedLogGroupName"},
            "DESTINATION_GROUP": {"Ref": "PilotLogGroup"}, "AGENT_ID": {"Ref": "AgentId"}, "EXPIRES_AT": {"Ref": "PilotExpiresAt"},
            "USERS": {"Fn::Join": ["", ["[\"", users[0], "\",\"", users[1], "\",\"", users[2], "\"]"]]}}}}}
    resources["AllowCloudWatchInvocation"] = {"Type": "AWS::Lambda::Permission", "Condition": "ForwardExisting", "Properties": {
        "Action": "lambda:InvokeFunction", "FunctionName": {"Fn::GetAtt": ["PilotLogForwarder", "Arn"]},
        "Principal": {"Fn::Sub": "logs.${AWS::Region}.amazonaws.com"}, "SourceAccount": {"Ref": "AWS::AccountId"}, "SourceArn": source_arn}}
    pattern = ["{ $.logType = \"CHAT_LOGS\" && $.agent_id = \"", {"Ref": "AgentId"}, "\" && ($.user_arn = \"", users[0],
               "\" || $.user_arn = \"", users[1], "\" || $.user_arn = \"", users[2], "\") }"]
    resources["PilotSubscription"] = {"Type": "AWS::Logs::SubscriptionFilter", "Condition": "ForwardExisting", "DependsOn": "AllowCloudWatchInvocation", "Properties": {
        "LogGroupName": {"Ref": "SharedLogGroupName"}, "FilterPattern": {"Fn::Join": ["", pattern]},
        "DestinationArn": {"Fn::GetAtt": ["PilotLogForwarder", "Arn"]}}}
    resources["QuickDeliverySource"] = {"Type": "AWS::Logs::DeliverySource", "Condition": "NewDeliverySource", "Properties": {
        "Name": {"Ref": "DeliverySourceName"}, "LogType": "CHAT_LOGS",
        "ResourceArn": {"Fn::Sub": "arn:${AWS::Partition}:quicksight:${AWS::Region}:${AWS::AccountId}:account/${AWS::AccountId}"}}}
    resources["QuickDeliveryDestination"] = {"Type": "AWS::Logs::DeliveryDestination", "Condition": "UseDelivery", "DependsOn": "PilotLogs", "Properties": {
        "Name": {"Fn::Sub": "${AWS::StackName}-destination"}, "DestinationResourceArn": group_arn, "OutputFormat": "json"}}
    resources["QuickDelivery"] = {"Type": "AWS::Logs::Delivery", "Condition": "UseDelivery", "Properties": {
        "DeliveryDestinationArn": {"Fn::GetAtt": ["QuickDeliveryDestination", "Arn"]},
        "DeliverySourceName": {"Fn::If": ["NewDeliverySource", {"Ref": "QuickDeliverySource"}, {"Ref": "DeliverySourceName"}]}}}
    t["Outputs"].update({"PilotLogGroupName": {"Value": {"Ref": "PilotLogs"}},
                         "Scope": {"Value": "Synthetic configuration copy only. Direct vended delivery is account-wide; filtered mode runs a customer-owned Lambda."}})
    return t


if __name__ == "__main__":
    path = ROOT / "deploy/amazon-quick/customer-pilot-v12.template.json"
    path.write_bytes((json.dumps(build(), indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"status": "template_generated_not_deployed", "path": str(path)}))
