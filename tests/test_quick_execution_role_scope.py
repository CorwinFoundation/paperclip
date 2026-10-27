"""Prevent the dedicated Quick worker's SSM transport from reading account data."""
import json
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "deploy/amazon-quick/backbond-client-execution.template.json"


def test_worker_management_cannot_read_parameters_documents_or_control_other_workers():
    props = json.loads(TEMPLATE.read_text())["Resources"]["ExecutionRole"]["Properties"]
    assert not props.get("ManagedPolicyArns")
    policy = next(p for p in props["Policies"] if p.get("PolicyName") == "PilotRunCommandTransportOnly")
    statements = policy["PolicyDocument"]["Statement"]
    actions = {a for s in statements for a in s["Action"]}
    assert actions == {
        "ssm:UpdateInstanceInformation",
        "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
        "ec2messages:AcknowledgeMessage", "ec2messages:DeleteMessage", "ec2messages:FailMessage",
        "ec2messages:GetEndpoint", "ec2messages:GetMessages", "ec2messages:SendReply",
    }
    # All account-data reads and command administration remain absent. A new
    # managed policy or wildcard action must fail this regression gate.
    assert not actions.intersection({"ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath",
        "ssm:GetDocument", "ssm:DescribeDocument", "ssm:DescribeAssociation", "ssm:ListAssociations",
        "ssm:SendCommand", "ssm:StartSession", "ssm:PutInventory", "ssm:PutComplianceItems"})
    for statement in statements:
        assert statement["Effect"] == "Allow"
        assert statement["Condition"]["DateLessThan"]["aws:CurrentTime"] == {"Ref": "PilotExpiresAt"}
        assert statement["Condition"]["StringEquals"]["aws:RequestedRegion"] == {"Ref": "AWS::Region"}
    def policy_statements(node):
        if isinstance(node, dict):
            if "PolicyDocument" in node:
                yield from node["PolicyDocument"]["Statement"]
            else:
                for child in node.values():
                    yield from policy_statements(child)
        elif isinstance(node, list):
            for child in node:
                yield from policy_statements(child)
    all_grants = list(policy_statements(props["Policies"]))
    all_actions = {a for s in all_grants for a in
                   (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])}
    assert all_actions == actions | {"s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:ListBucket", "sts:AssumeRole"}
    assert all(s["Condition"]["DateLessThan"]["aws:CurrentTime"] == {"Ref": "PilotExpiresAt"} for s in all_grants)


def test_management_reduction_preserves_per_client_evidence_and_assumption_boundaries():
    props = json.loads(TEMPLATE.read_text())["Resources"]["ExecutionRole"]["Properties"]
    assert props["AssumeRolePolicyDocument"]["Statement"] == [{"Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]
    assert props["MaxSessionDuration"] == 3600
    evidence = props["Policies"][0]
    assert evidence["PolicyName"] == "OwnCustomerEvidenceOnly"
    grants = evidence["PolicyDocument"]["Statement"]
    assert grants[0]["Resource"] == {"Fn::Sub": "arn:${AWS::Partition}:s3:::${EvidenceBucket}/pilots/${CustomerSlug}/*"}
    assert all(s["Condition"]["DateLessThan"]["aws:CurrentTime"] == {"Ref": "PilotExpiresAt"} for s in grants)
    assumption = props["Policies"][1]["Fn::If"][1]
    assert assumption["PolicyName"] == "AssumeOnlyThisCustomerPilot"
    grant = assumption["PolicyDocument"]["Statement"][0]
    assert grant["Action"] == "sts:AssumeRole" and grant["Resource"] == {"Ref": "CustomerPilotRoleArn"}
    assert grant["Condition"]["StringEquals"]["sts:ExternalId"] == {"Ref": "CustomerExternalId"}
    assert grant["Condition"]["DateLessThan"]["aws:CurrentTime"] == {"Ref": "PilotExpiresAt"}
