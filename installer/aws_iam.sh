#!/usr/bin/env bash
# bioAF AWS IAM documents owned by the installer (security plan 04B).
#
# The app's own permissions and every permission an app-created role can hold are customer-managed
# policies that only the installer (an operator) writes:
#
#   bioaf-app-access-core      bioaf-app: reads, data, and EC2 limited to this installation
#   bioaf-app-access-control   bioaf-app: EKS and IAM, plus the denials
#   bioaf-app-access-services  bioaf-app: ECR, CodeBuild and logs
#   bioaf-app-created-role-boundary
#                              the permissions boundary on every role the app creates
#   bioaf-role-s3-readwrite / bioaf-role-s3-readonly / bioaf-role-cluster-autoscaler /
#   bioaf-role-image-build     the only customer policies the app may attach to its roles
#
# The app cannot write a role policy itself (no iam:PutRolePolicy): an app-created role can hold
# only AWS's EKS policies and the bioaf-role-* policies above, capped by the boundary, which carries
# the same ownership denials as the app. New roles live under the /bioaf/ path, which only
# CreateRole sets and nothing can change; roles from before (path /) count as the app's only once an
# operator has tagged them during reconciliation, and the app can never add that tag to an IAM
# resource outside /bioaf/.
#
# aws_iam_installation_id ACCOUNT REGION   ownership tag value (backend/app/platform/aws_installation.py)
# aws_iam_policy_arn ACCOUNT NAME          a customer-managed policy ARN
# aws_iam_policy_document NAME ACCOUNT REGION APP_VPC DEFAULT_VPC
#                                          render one of the documents above (compact JSON)
# aws_iam_put_app_policy ACCOUNT REGION APP_VPC DEFAULT_VPC ROLE
#                                          attach the app's managed policies, then remove the app
#                                          role's old inline policies
# aws_iam_ensure_policies ACCOUNT REGION APP_VPC DEFAULT_VPC
#                                          create or update every document (the boundary first)

AWS_IAM_POLICIES="bioaf-app-created-role-boundary bioaf-role-s3-readwrite bioaf-role-s3-readonly bioaf-role-cluster-autoscaler bioaf-role-image-build bioaf-app-access-core bioaf-app-access-control bioaf-app-access-services"
AWS_IAM_APP_POLICIES="bioaf-app-access-core bioaf-app-access-control bioaf-app-access-services"

aws_iam_installation_id() { printf '%s-%s' "$1" "$2"; }
aws_iam_policy_arn() { printf 'arn:aws:iam::%s:policy/%s' "$1" "$2"; }
aws_iam_boundary_name() { printf 'bioaf-app-created-role-boundary'; }
aws_iam_boundary_arn() { aws_iam_policy_arn "$1" "$(aws_iam_boundary_name)"; }

aws_iam_policy_document() {
    local name="$1" a="$2" r="$3" vpc="${4:-}" dvpc="${5:-}" id ec2 owned tagged retag share logdel
    id="$(aws_iam_installation_id "$a" "$r")"
    ec2="arn:aws:ec2:${r}:${a}"
    owned="\"StringEquals\":{\"aws:ResourceTag/bioaf:installation\":\"${id}\"}"
    tagged="\"StringEquals\":{\"aws:RequestTag/bioaf:installation\":\"${id}\"}"
    # Denials shared by the app and every role it creates.
    retag='{"Sid":"DenyOwnershipRetag","Effect":"Deny","Action":["ec2:CreateTags","ec2:DeleteTags"],"Resource":"*","Condition":{"Null":{"ec2:CreateAction":"true"},"ForAnyValue:StringEquals":{"aws:TagKeys":["bioaf:installation"]}}}'
    share='{"Sid":"DenySharing","Effect":"Deny","Action":["ec2:ModifyImageAttribute","ec2:ModifySnapshotAttribute"],"Resource":"*"}'
    logdel='{"Sid":"DenyLogHistoryDelete","Effect":"Deny","Action":"logs:DeleteLogGroup","Resource":"*"}'
    case "$name" in
    bioaf-app-created-role-boundary)
        cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"Read","Effect":"Allow","Action":["ec2:Describe*","ec2:GetInstanceTypesFromInstanceRequirements","elasticloadbalancing:Describe*","autoscaling:Describe*","eks:Describe*","eks:List*","ecr:GetAuthorizationToken","ecr:BatchCheckLayerAvailability","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:DescribeImages","ecr:DescribeRepositories","ecr:ListImages","ssm:GetParameter","ssm:GetParameters","sts:GetCallerIdentity","kms:DescribeKey"],"Resource":"*"},
{"Sid":"ClusterAndNodes","Effect":"Allow","Action":["ec2:AttachVolume","ec2:DetachVolume","ec2:CreateVolume","ec2:ModifyVolume","ec2:DeleteVolume","ec2:CreateSnapshot","ec2:DeleteSnapshot","ec2:CreateTags","ec2:AuthorizeSecurityGroupIngress","ec2:RevokeSecurityGroupIngress","ec2:CreateSecurityGroup","ec2:DeleteSecurityGroup","ec2:CreateRoute","ec2:DeleteRoute","ec2:ModifyInstanceAttribute","ec2:AssignPrivateIpAddresses","ec2:UnassignPrivateIpAddresses","ec2:AssignIpv6Addresses","ec2:UnassignIpv6Addresses","ec2:AttachNetworkInterface","ec2:DetachNetworkInterface","ec2:CreateNetworkInterface","ec2:DeleteNetworkInterface","ec2:ModifyNetworkInterfaceAttribute","elasticloadbalancing:*","autoscaling:UpdateAutoScalingGroup","autoscaling:SetDesiredCapacity","autoscaling:TerminateInstanceInAutoScalingGroup","iam:CreateServiceLinkedRole"],"Resource":"*"},
{"Sid":"Builder","Effect":"Allow","Action":["ec2:RunInstances","ec2:StopInstances","ec2:TerminateInstances","ec2:CreateImage","ec2:RegisterImage","ec2:CreateKeyPair","ec2:DeleteKeyPair"],"Resource":"*"},
{"Sid":"Keys","Effect":"Allow","Action":["kms:CreateGrant","kms:ListGrants","kms:RevokeGrant","kms:Decrypt","kms:Encrypt","kms:ReEncrypt*","kms:GenerateDataKey*"],"Resource":"*"},
{"Sid":"ImagesPush","Effect":"Allow","Action":["ecr:InitiateLayerUpload","ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage","ecr:CreateRepository"],"Resource":"arn:aws:ecr:${r}:${a}:repository/bioaf-*"},
{"Sid":"Data","Effect":"Allow","Action":"s3:*","Resource":["arn:aws:s3:::bioaf-*","arn:aws:s3:::bioaf-*/*"],"Condition":{"StringEquals":{"aws:ResourceAccount":"${a}"}}},
{"Sid":"DenyUnownedInstances","Effect":"Deny","Action":["ec2:TerminateInstances","ec2:StopInstances"],"Resource":"*","Condition":{"StringNotEquals":{"aws:ResourceTag/bioaf:installation":"${id}"}}},
{"Sid":"DenyUnownedStorage","Effect":"Deny","Action":["ec2:DeleteVolume","ec2:DeleteSnapshot"],"Resource":"*","Condition":{"StringNotEquals":{"aws:ResourceTag/bioaf:installation":"${id}","aws:ResourceTag/ebs.csi.aws.com/cluster":"true"}}},
{"Sid":"DenyImageRemoval","Effect":"Deny","Action":"ec2:DeregisterImage","Resource":"*"},
${retag},${share},${logdel}
]}
JSON
        ;;
    bioaf-role-s3-readwrite)
        cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"Objects","Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":"arn:aws:s3:::bioaf-*/*","Condition":{"StringEquals":{"aws:ResourceAccount":"${a}"}}},
{"Sid":"Buckets","Effect":"Allow","Action":["s3:ListBucket","s3:GetBucketLocation"],"Resource":"arn:aws:s3:::bioaf-*","Condition":{"StringEquals":{"aws:ResourceAccount":"${a}"}}}
]}
JSON
        ;;
    bioaf-role-s3-readonly)
        cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"Objects","Effect":"Allow","Action":"s3:GetObject","Resource":"arn:aws:s3:::bioaf-*/*","Condition":{"StringEquals":{"aws:ResourceAccount":"${a}"}}},
{"Sid":"Buckets","Effect":"Allow","Action":["s3:ListBucket","s3:GetBucketLocation"],"Resource":"arn:aws:s3:::bioaf-*","Condition":{"StringEquals":{"aws:ResourceAccount":"${a}"}}}
]}
JSON
        ;;
    bioaf-role-cluster-autoscaler)
        cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"Discover","Effect":"Allow","Action":["autoscaling:DescribeAutoScalingGroups","autoscaling:DescribeAutoScalingInstances","autoscaling:DescribeLaunchConfigurations","autoscaling:DescribeScalingActivities","autoscaling:DescribeTags","ec2:DescribeImages","ec2:DescribeInstanceTypes","ec2:DescribeLaunchTemplateVersions","ec2:GetInstanceTypesFromInstanceRequirements"],"Resource":"*"},
{"Sid":"NodeGroups","Effect":"Allow","Action":"eks:DescribeNodegroup","Resource":"arn:aws:eks:${r}:${a}:nodegroup/bioaf-*"},
{"Sid":"Scale","Effect":"Allow","Action":["autoscaling:SetDesiredCapacity","autoscaling:TerminateInstanceInAutoScalingGroup"],"Resource":"*","Condition":{"StringEquals":{"aws:ResourceTag/k8s.io/cluster-autoscaler/enabled":"true"},"StringLike":{"aws:ResourceTag/eks:cluster-name":"bioaf-*"}}}
]}
JSON
        ;;
    bioaf-role-image-build)
        cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"EcrAuth","Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
{"Sid":"EcrPush","Effect":"Allow","Action":["ecr:BatchCheckLayerAvailability","ecr:InitiateLayerUpload","ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:DescribeRepositories","ecr:CreateRepository"],"Resource":"arn:aws:ecr:${r}:${a}:repository/bioaf-*"},
{"Sid":"ReadBuildContext","Effect":"Allow","Action":["s3:GetObject","s3:GetObjectVersion"],"Resource":"arn:aws:s3:::bioaf-*/*","Condition":{"StringEquals":{"aws:ResourceAccount":"${a}"}}},
{"Sid":"BuildLogs","Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":["arn:aws:logs:${r}:${a}:log-group:/aws/codebuild/bioaf-image-build","arn:aws:logs:${r}:${a}:log-group:/aws/codebuild/bioaf-image-build:*"]},
{"Sid":"PackerDescribe","Effect":"Allow","Action":["ec2:DescribeImageAttribute","ec2:DescribeImages","ec2:DescribeInstances","ec2:DescribeInstanceStatus","ec2:DescribeRegions","ec2:DescribeSecurityGroups","ec2:DescribeSnapshots","ec2:DescribeSubnets","ec2:DescribeTags","ec2:DescribeVolumes","ec2:DescribeVpcs","ec2:DescribeAvailabilityZones","ec2:DescribeKeyPairs"],"Resource":"*"},
{"Sid":"PackerLaunchOwned","Effect":"Allow","Action":"ec2:RunInstances","Resource":["${ec2}:instance/*","${ec2}:volume/*"],"Condition":{${tagged}}},
{"Sid":"PackerLaunchNetwork","Effect":"Allow","Action":"ec2:RunInstances","Resource":["${ec2}:subnet/*","${ec2}:security-group/*"],"Condition":{"StringEquals":{"ec2:Vpc":"${ec2}:vpc/${dvpc}"}}},
{"Sid":"PackerLaunchSourceImage","Effect":"Allow","Action":"ec2:RunInstances","Resource":"arn:aws:ec2:${r}::image/*","Condition":{"StringEquals":{"ec2:Owner":"099720109477"}}},
{"Sid":"PackerLaunchKeyAndInterface","Effect":"Allow","Action":"ec2:RunInstances","Resource":["${ec2}:key-pair/packer_*","${ec2}:network-interface/*"]},
{"Sid":"PackerTemporaryKeyPair","Effect":"Allow","Action":["ec2:CreateKeyPair","ec2:DeleteKeyPair"],"Resource":"${ec2}:key-pair/packer_*"},
{"Sid":"PackerTagAtLaunch","Effect":"Allow","Action":"ec2:CreateTags","Resource":["${ec2}:instance/*","${ec2}:volume/*","${ec2}:network-interface/*"],"Condition":{"StringEquals":{"ec2:CreateAction":"RunInstances"}}},
{"Sid":"PackerOwnedBuilder","Effect":"Allow","Action":["ec2:StopInstances","ec2:TerminateInstances","ec2:CreateImage","ec2:AttachVolume","ec2:DetachVolume","ec2:DeleteVolume"],"Resource":["${ec2}:instance/*","${ec2}:volume/*"],"Condition":{${owned}}},
{"Sid":"PackerNewImage","Effect":"Allow","Action":["ec2:CreateImage","ec2:RegisterImage"],"Resource":["arn:aws:ec2:${r}::image/*","arn:aws:ec2:${r}::snapshot/*"]},
{"Sid":"PackerTagImage","Effect":"Allow","Action":"ec2:CreateTags","Resource":["arn:aws:ec2:${r}::image/*","arn:aws:ec2:${r}::snapshot/*"],"Condition":{"ForAllValues:StringNotEquals":{"aws:TagKeys":["bioaf:installation"]}}},
${retag},${share}
]}
JSON
        ;;
    bioaf-app-access-core)
        cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"Read","Effect":"Allow","Action":["ec2:Describe*","ec2:GetInstanceTypesFromInstanceRequirements","ec2:GetManagedPrefixListEntries","eks:Describe*","eks:List*","autoscaling:Describe*","iam:Get*","iam:List*","logs:Describe*","ecr:GetAuthorizationToken","ecr:Describe*","codebuild:BatchGet*","ce:GetCostAndUsage","s3:ListAllMyBuckets","sts:GetCallerIdentity"],"Resource":"*"},
{"Sid":"Data","Effect":"Allow","Action":"s3:*","Resource":["arn:aws:s3:::bioaf-*","arn:aws:s3:::bioaf-*/*"],"Condition":{"StringEquals":{"aws:ResourceAccount":"${a}"}}},
{"Sid":"Ec2CreateOwnedNetwork","Effect":"Allow","Action":["ec2:CreateVpc","ec2:CreateInternetGateway","ec2:CreateLaunchTemplate"],"Resource":["${ec2}:vpc/*","${ec2}:internet-gateway/*","${ec2}:launch-template/*"],"Condition":{${tagged}}},
{"Sid":"Ec2CreateOwnedPeering","Effect":"Allow","Action":"ec2:CreateVpcPeeringConnection","Resource":"${ec2}:vpc-peering-connection/*","Condition":{${tagged}}},
{"Sid":"Ec2CreateOwnedInVpc","Effect":"Allow","Action":["ec2:CreateSubnet","ec2:CreateRouteTable","ec2:CreateSecurityGroup"],"Resource":["${ec2}:subnet/*","${ec2}:route-table/*","${ec2}:security-group/*"],"Condition":{${tagged}}},
{"Sid":"Ec2LaunchOwned","Effect":"Allow","Action":"ec2:RunInstances","Resource":["${ec2}:instance/*","${ec2}:volume/*","${ec2}:network-interface/*"],"Condition":{${tagged}}},
{"Sid":"Ec2ChangeOwned","Effect":"Allow","Action":["ec2:Create*","ec2:Delete*","ec2:Modify*","ec2:Attach*","ec2:Detach*","ec2:Associate*","ec2:Disassociate*","ec2:Replace*","ec2:Authorize*","ec2:Revoke*","ec2:Update*","ec2:AcceptVpcPeeringConnection","ec2:RunInstances","ec2:TerminateInstances","ec2:StopInstances","ec2:StartInstances","ec2:RebootInstances"],"Resource":"${ec2}:*","Condition":{${owned}}},
{"Sid":"Ec2RuleObjects","Effect":"Allow","Action":["ec2:Authorize*","ec2:Revoke*","ec2:ModifySecurityGroupRules"],"Resource":"${ec2}:security-group-rule/*"},
{"Sid":"Ec2WorkNodeImages","Effect":"Allow","Action":"ec2:RunInstances","Resource":"arn:aws:ec2:${r}::image/*","Condition":{"StringEquals":{"ec2:Owner":"${a}"}}},
{"Sid":"AppNetwork","Effect":"Allow","Action":["ec2:CreateSecurityGroup","ec2:DeleteSecurityGroup","ec2:Authorize*","ec2:Revoke*","ec2:ModifySecurityGroupRules","ec2:UpdateSecurityGroupRuleDescriptions*","ec2:ModifyInstanceAttribute","ec2:CreateRoute","ec2:ReplaceRoute","ec2:DeleteRoute"],"Resource":["${ec2}:security-group/*","${ec2}:route-table/*"],"Condition":{"StringEquals":{"ec2:Vpc":"${ec2}:vpc/${vpc}"}}},
{"Sid":"AppVpc","Effect":"Allow","Action":["ec2:CreateSecurityGroup","ec2:CreateVpcPeeringConnection","ec2:AcceptVpcPeeringConnection"],"Resource":"${ec2}:vpc/${vpc}"},
{"Sid":"ClusterSecurityGroup","Effect":"Allow","Action":["ec2:AuthorizeSecurityGroupIngress","ec2:RevokeSecurityGroupIngress"],"Resource":"${ec2}:security-group/*","Condition":{"StringLike":{"aws:ResourceTag/aws:eks:cluster-name":"bioaf-*"}}},
{"Sid":"Ec2TagAtCreate","Effect":"Allow","Action":"ec2:CreateTags","Resource":"${ec2}:*","Condition":{"StringEquals":{"ec2:CreateAction":["CreateVpc","CreateInternetGateway","CreateVpcPeeringConnection","CreateLaunchTemplate","CreateSubnet","CreateRouteTable","CreateSecurityGroup","RunInstances","AuthorizeSecurityGroupIngress","AuthorizeSecurityGroupEgress"]}}},
${retag},${share}
]}
JSON
        ;;
    bioaf-app-access-control)
        local iam="arn:aws:iam::${a}" boundary bounded
        boundary="$(aws_iam_boundary_arn "$a")"
        bounded="\"StringEquals\":{\"iam:PermissionsBoundary\":\"${boundary}\"}"
        cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"Eks","Effect":"Allow","Action":"eks:*","Resource":["arn:aws:eks:${r}:${a}:cluster/bioaf-*","arn:aws:eks:${r}:${a}:nodegroup/bioaf-*","arn:aws:eks:${r}:${a}:addon/bioaf-*","arn:aws:eks:${r}:${a}:access-entry/bioaf-*"]},
{"Sid":"NodeGroupTags","Effect":"Allow","Action":["autoscaling:CreateOrUpdateTags","autoscaling:DeleteTags"],"Resource":"*","Condition":{"StringLike":{"aws:ResourceTag/eks:cluster-name":"bioaf-*"}}},
{"Sid":"RoleCreate","Effect":"Allow","Action":"iam:CreateRole","Resource":"${iam}:role/bioaf/*","Condition":{${bounded}}},
{"Sid":"RoleAttach","Effect":"Allow","Action":"iam:AttachRolePolicy","Resource":["${iam}:role/bioaf/*","${iam}:role/bioaf-*"],"Condition":{${bounded},"ArnLike":{"iam:PolicyARN":["arn:aws:iam::aws:policy/AmazonEKS*","arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly","arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy","${iam}:policy/bioaf-role-*"]}}},
{"Sid":"RoleManageNew","Effect":"Allow","Action":["iam:DetachRolePolicy","iam:DeleteRolePolicy","iam:DeleteRole","iam:TagRole","iam:UntagRole","iam:UpdateAssumeRolePolicy","iam:UpdateRoleDescription"],"Resource":"${iam}:role/bioaf/*"},
{"Sid":"RoleManageOwned","Effect":"Allow","Action":["iam:DetachRolePolicy","iam:DeleteRolePolicy","iam:DeleteRole","iam:TagRole","iam:UntagRole","iam:UpdateAssumeRolePolicy","iam:UpdateRoleDescription"],"Resource":"${iam}:role/bioaf-*","Condition":{${owned}}},
{"Sid":"PassNew","Effect":"Allow","Action":"iam:PassRole","Resource":"${iam}:role/bioaf/*","Condition":{"StringEquals":{"iam:PassedToService":["eks.amazonaws.com","ec2.amazonaws.com","codebuild.amazonaws.com"]}}},
{"Sid":"PassOwned","Effect":"Allow","Action":"iam:PassRole","Resource":"${iam}:role/bioaf-*","Condition":{"StringEquals":{"aws:ResourceTag/bioaf:installation":"${id}","iam:PassedToService":["eks.amazonaws.com","ec2.amazonaws.com","codebuild.amazonaws.com"]}}},
{"Sid":"ProfilesNew","Effect":"Allow","Action":["iam:CreateInstanceProfile","iam:DeleteInstanceProfile","iam:AddRoleToInstanceProfile","iam:RemoveRoleFromInstanceProfile","iam:TagInstanceProfile","iam:UntagInstanceProfile"],"Resource":"${iam}:instance-profile/bioaf/*"},
{"Sid":"ProfilesOwned","Effect":"Allow","Action":["iam:DeleteInstanceProfile","iam:AddRoleToInstanceProfile","iam:RemoveRoleFromInstanceProfile","iam:TagInstanceProfile","iam:UntagInstanceProfile"],"Resource":"${iam}:instance-profile/bioaf-*","Condition":{${owned}}},
{"Sid":"ClusterIdentity","Effect":"Allow","Action":["iam:CreateOpenIDConnectProvider","iam:DeleteOpenIDConnectProvider","iam:TagOpenIDConnectProvider","iam:UntagOpenIDConnectProvider","iam:UpdateOpenIDConnectProviderThumbprint","iam:AddClientIDToOpenIDConnectProvider"],"Resource":"${iam}:oidc-provider/oidc.eks.${r}.amazonaws.com/*"},
{"Sid":"ServiceLinkedRoles","Effect":"Allow","Action":"iam:CreateServiceLinkedRole","Resource":"*","Condition":{"StringEquals":{"iam:AWSServiceName":["eks.amazonaws.com","eks-nodegroup.amazonaws.com","autoscaling.amazonaws.com","elasticloadbalancing.amazonaws.com","spot.amazonaws.com"]}}},
{"Sid":"BuildLogs","Effect":"Allow","Action":["logs:GetLogEvents","logs:FilterLogEvents"],"Resource":["arn:aws:logs:${r}:${a}:log-group:/aws/codebuild/bioaf-*","arn:aws:logs:${r}:${a}:log-group:/aws/codebuild/bioaf-*:*"]},
{"Sid":"DenySelfAndPolicies","Effect":"Deny","Action":["iam:Put*","iam:Delete*","iam:Attach*","iam:Detach*","iam:Update*","iam:Tag*","iam:Untag*","iam:Create*","iam:Add*","iam:Remove*","iam:SetDefaultPolicyVersion","iam:PassRole"],"Resource":["${iam}:role/bioaf-app","${iam}:instance-profile/bioaf-app","${iam}:policy/bioaf-*"]},
{"Sid":"DenyProtected","Effect":"Deny","Action":["iam:Put*","iam:Delete*","iam:Attach*","iam:Detach*","iam:Update*","iam:Tag*","iam:Untag*","iam:PassRole","iam:AddRoleToInstanceProfile"],"Resource":"*","Condition":{"StringEquals":{"aws:ResourceTag/bioaf:protected":"true"}}},
{"Sid":"DenyRolePolicyWritesAndBoundaryChanges","Effect":"Deny","Action":["iam:PutRolePolicy","iam:PutRolePermissionsBoundary","iam:DeleteRolePermissionsBoundary"],"Resource":"*"},
{"Sid":"DenyIamOwnershipOutsidePath","Effect":"Deny","Action":["iam:TagRole","iam:UntagRole","iam:TagInstanceProfile","iam:UntagInstanceProfile"],"NotResource":["${iam}:role/bioaf/*","${iam}:instance-profile/bioaf/*"],"Condition":{"ForAnyValue:StringEquals":{"aws:TagKeys":["bioaf:installation","bioaf:protected"]}}},
${logdel}
]}
JSON
        ;;
    bioaf-app-access-services)
        cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"Images","Effect":"Allow","Action":["ecr:CreateRepository","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:ListImages","ecr:TagResource"],"Resource":"arn:aws:ecr:${r}:${a}:repository/bioaf-*"},
{"Sid":"Builds","Effect":"Allow","Action":["codebuild:CreateProject","codebuild:UpdateProject","codebuild:DeleteProject","codebuild:StartBuild","codebuild:StopBuild"],"Resource":"arn:aws:codebuild:${r}:${a}:project/bioaf-*"},
{"Sid":"BuildLogs","Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":["arn:aws:logs:${r}:${a}:log-group:/aws/codebuild/bioaf-*","arn:aws:logs:${r}:${a}:log-group:/aws/codebuild/bioaf-*:*"]},
{"Sid":"ControlPlaneLogGroup","Effect":"Allow","Action":["logs:CreateLogGroup","logs:PutRetentionPolicy","logs:DeleteRetentionPolicy","logs:TagResource","logs:UntagResource","logs:ListTagsForResource","logs:TagLogGroup","logs:UntagLogGroup","logs:ListTagsLogGroup"],"Resource":["arn:aws:logs:${r}:${a}:log-group:/aws/eks/bioaf-*/cluster","arn:aws:logs:${r}:${a}:log-group:/aws/eks/bioaf-*/cluster:*"]},
${logdel}
]}
JSON
        ;;
    *) return 1 ;;
    esac | tr -d ' \n'
}

# aws_iam_ensure_policy ACCOUNT NAME DOCFILE
#     Create the customer-managed policy, or make DOCFILE its default version (pruning the oldest
#     version when IAM's five-version limit is reached).
aws_iam_ensure_policy() {
    local a="$1" name="$2" doc="$3" arn oldest
    arn="$(aws_iam_policy_arn "$a" "$name")"
    if aws iam get-policy --policy-arn "$arn" >/dev/null 2>&1; then
        oldest="$(aws iam list-policy-versions --policy-arn "$arn" \
            --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text 2>/dev/null | awk '{ if (NF >= 4) print $NF }')"
        [ -z "$oldest" ] || aws iam delete-policy-version --policy-arn "$arn" --version-id "$oldest" || return 1
        aws iam create-policy-version --policy-arn "$arn" --policy-document "file://${doc}" --set-as-default >/dev/null
    else
        aws iam create-policy --policy-name "$name" --policy-document "file://${doc}" \
            --description "bioAF installer-managed policy (installer/aws_iam.sh)" >/dev/null
    fi
}

# aws_iam_ensure_policies ACCOUNT REGION APP_VPC DEFAULT_VPC
#     Create or update every installer-owned policy (the boundary first).
aws_iam_ensure_policies() {
    local a="$1" r="$2" vpc="$3" dvpc="$4" name doc
    for name in $AWS_IAM_POLICIES; do
        doc="$(mktemp -t bioaf-policy-XXXXXX)"
        aws_iam_policy_document "$name" "$a" "$r" "$vpc" "$dvpc" > "$doc" || { rm -f "$doc"; return 1; }
        aws_iam_ensure_policy "$a" "$name" "$doc" || { rm -f "$doc"; return 1; }
        rm -f "$doc"
    done
}

# aws_iam_put_app_policy ACCOUNT REGION APP_VPC DEFAULT_VPC ROLE
#     After aws_iam_ensure_policies: attach the app's managed policies, then delete the role's old
#     inline policies. Managed policies do not count toward the 10,240-character inline quota, so
#     the starter policy stays in place until the new ones are attached.
aws_iam_put_app_policy() {
    local a="$1" role="$5" name
    for name in $AWS_IAM_APP_POLICIES; do
        aws iam attach-role-policy --role-name "$role" --policy-arn "$(aws_iam_policy_arn "$a" "$name")" || return 1
    done
    for name in bioaf-app-starter bioaf-app; do
        if aws iam get-role-policy --role-name "$role" --policy-name "$name" >/dev/null 2>&1; then
            aws iam delete-role-policy --role-name "$role" --policy-name "$name" || return 1
        fi
    done
}

# aws_iam_owned_roles ROLE
#     The roles the app created before the /bioaf/ path (bioaf-* at path / tagged managed_by=bioaf),
#     one per line, excluding ROLE.
aws_iam_owned_roles() {
    local app_role="$1" name
    for name in $(aws iam list-roles --path-prefix / --query "Roles[?Path=='/' && starts_with(RoleName, 'bioaf-')].RoleName" --output text); do
        [ "$name" = "$app_role" ] && continue
        if aws iam list-role-tags --role-name "$name" \
            --query "Tags[?Key=='managed_by' && Value=='bioaf'] | length(@)" --output text | grep -qx '1'; then
            printf '%s\n' "$name"
        fi
    done
}

# aws_iam_owned_profiles ROLE
#     Instance profiles at path / that hold one of the owned roles, one per line.
aws_iam_owned_profiles() {
    local roles="$1" name
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        aws iam list-instance-profiles-for-role --role-name "$name" \
            --query "InstanceProfiles[?Path=='/'].InstanceProfileName" --output text | tr '\t' '\n'
    done < "$roles" | sed '/^$/d; /^None$/d' | sort -u
}

# aws_iam_owned_ec2 REGION
#     EC2 resource ARNs bioAF created in REGION (managed_by=bioaf or bioaf-managed=true), one per line.
#     Images and snapshots are excluded: nothing the app does needs them owned.
aws_iam_owned_ec2() {
    local r="$1"
    {
        aws resourcegroupstaggingapi get-resources --region "$r" --resource-type-filters ec2 \
            --tag-filters Key=managed_by,Values=bioaf --query 'ResourceTagMappingList[].ResourceARN' --output text
        aws resourcegroupstaggingapi get-resources --region "$r" --resource-type-filters ec2 \
            --tag-filters Key=bioaf-managed,Values=true --query 'ResourceTagMappingList[].ResourceARN' --output text
    } | tr '\t' '\n' | sed '/^$/d; /^None$/d; /:image\//d; /:snapshot\//d' | sort -u
}

# aws_iam_claim ACCOUNT REGION ROLES_FILE PROFILES_FILE EC2_FILE [INSTANCE_ID]
#     Put the boundary on each listed role and tag it, the listed instance profiles, the EC2 ARNs and
#     the application VM with this installation's ownership tag. Uses the operator's credentials,
#     never the app's: the app cannot add this tag to IAM resources outside /bioaf/.
aws_iam_claim() {
    local a="$1" r="$2" roles="$3" profiles="$4" arns="$5" vm="${6:-}" id boundary name batch
    id="$(aws_iam_installation_id "$a" "$r")"
    boundary="$(aws_iam_boundary_arn "$a")"
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        aws iam put-role-permissions-boundary --role-name "$name" --permissions-boundary "$boundary" || return 1
        aws iam tag-role --role-name "$name" --tags "Key=bioaf:installation,Value=${id}" || return 1
    done < "$roles"
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        aws iam tag-instance-profile --instance-profile-name "$name" --tags "Key=bioaf:installation,Value=${id}" || return 1
    done < "$profiles"
    batch=()
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        batch+=("$name")
        if [ "${#batch[@]}" -eq 20 ]; then
            aws resourcegroupstaggingapi tag-resources --region "$r" --resource-arn-list "${batch[@]}" \
                --tags "bioaf:installation=${id}" >/dev/null || return 1
            batch=()
        fi
    done < "$arns"
    if [ "${#batch[@]}" -gt 0 ]; then
        aws resourcegroupstaggingapi tag-resources --region "$r" --resource-arn-list "${batch[@]}" \
            --tags "bioaf:installation=${id}" >/dev/null || return 1
    fi
    if [ -n "$vm" ]; then
        aws ec2 create-tags --region "$r" --resources "$vm" --tags "Key=bioaf:installation,Value=${id}" || return 1
    fi
}
