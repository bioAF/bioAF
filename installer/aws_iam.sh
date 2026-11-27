#!/usr/bin/env bash
# bioAF AWS IAM documents owned by the installer (security plan 04B).
#
# aws_iam_installation_id ACCOUNT REGION
#     The ownership tag value, bioaf:installation=<account>-<region>. The app, the Terraform modules
#     and this installer derive it the same way (backend/app/platform/aws_installation.py).
# aws_iam_boundary_name / aws_iam_boundary_arn ACCOUNT
#     The permissions boundary every role the app creates must carry.
# aws_iam_role_boundary_policy ACCOUNT REGION
#     That boundary: the most any app-created role (cluster, node, runner, Work Node, autoscaler,
#     EBS CSI, CodeBuild) may ever do. No IAM writes, no secrets, no role assumption.
# aws_iam_app_policy ACCOUNT REGION APP_VPC_ID
#     The bioaf-app role's policy. Reads stay account-wide where AWS cannot scope them; every
#     mutation is limited to this installation: EC2 resources it tagged at creation, the
#     application VPC's groups and routes, bioaf-* EKS/ECR/CodeBuild/log resources in this account
#     and region, and IAM roles created under the boundary. The app cannot edit its own role, the
#     boundary, protected operator roles, or pass roles to unapproved services.
#
# The documents are compact JSON (IAM counts characters against the inline-policy limit).

aws_iam_installation_id() { printf '%s-%s' "$1" "$2"; }
aws_iam_boundary_name() { printf 'bioaf-app-created-role-boundary'; }
aws_iam_boundary_arn() { printf 'arn:aws:iam::%s:policy/%s' "$1" "$(aws_iam_boundary_name)"; }

aws_iam_role_boundary_policy() {
    local a="$1" r="$2"
    cat <<JSON | tr -d ' \n'
{"Version":"2012-10-17","Statement":[
{"Sid":"Compute","Effect":"Allow","Action":["ec2:*","elasticloadbalancing:*","autoscaling:*","eks:Describe*","eks:List*","eks:AccessKubernetesApi","cloudwatch:PutMetricData","ssm:GetParameter","ssm:GetParameters","sts:GetCallerIdentity"],"Resource":"*"},
{"Sid":"Keys","Effect":"Allow","Action":["kms:CreateGrant","kms:ListGrants","kms:RevokeGrant","kms:Decrypt","kms:Encrypt","kms:ReEncrypt*","kms:GenerateDataKey*","kms:DescribeKey"],"Resource":"*"},
{"Sid":"ImagesRead","Effect":"Allow","Action":["ecr:GetAuthorizationToken","ecr:BatchCheckLayerAvailability","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:DescribeImages","ecr:DescribeRepositories","ecr:ListImages"],"Resource":"*"},
{"Sid":"ImagesPush","Effect":"Allow","Action":["ecr:InitiateLayerUpload","ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage","ecr:CreateRepository"],"Resource":"arn:aws:ecr:${r}:${a}:repository/bioaf-*"},
{"Sid":"Data","Effect":"Allow","Action":"s3:*","Resource":["arn:aws:s3:::bioaf-*","arn:aws:s3:::bioaf-*/*"],"Condition":{"StringEquals":{"aws:ResourceAccount":"${a}"}}},
{"Sid":"Logs","Effect":"Allow","Action":"logs:*","Resource":"arn:aws:logs:${r}:${a}:*"},
{"Sid":"ServiceRoles","Effect":"Allow","Action":["iam:CreateServiceLinkedRole","iam:GetRole","iam:ListAttachedRolePolicies"],"Resource":"*"}
]}
JSON
}

aws_iam_app_policy() {
    local a="$1" r="$2" vpc="$3" id boundary ec2 owned tagged
    id="$(aws_iam_installation_id "$a" "$r")"
    boundary="$(aws_iam_boundary_arn "$a")"
    ec2="arn:aws:ec2:${r}:${a}"
    owned="\"StringEquals\":{\"aws:ResourceTag/bioaf:installation\":\"${id}\"}"
    tagged="\"StringEquals\":{\"aws:RequestTag/bioaf:installation\":\"${id}\"}"
    cat <<JSON | tr -d ' \n'
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
{"Sid":"Eks","Effect":"Allow","Action":"eks:*","Resource":["arn:aws:eks:${r}:${a}:cluster/bioaf-*","arn:aws:eks:${r}:${a}:nodegroup/bioaf-*","arn:aws:eks:${r}:${a}:addon/bioaf-*","arn:aws:eks:${r}:${a}:access-entry/bioaf-*"]},
{"Sid":"NodeGroupTags","Effect":"Allow","Action":["autoscaling:CreateOrUpdateTags","autoscaling:DeleteTags"],"Resource":"*","Condition":{"StringLike":{"aws:ResourceTag/eks:cluster-name":"bioaf-*"}}},
{"Sid":"RoleCreate","Effect":"Allow","Action":"iam:CreateRole","Resource":"arn:aws:iam::${a}:role/bioaf-*","Condition":{"StringEquals":{"iam:PermissionsBoundary":"${boundary}"}}},
{"Sid":"RolePolicies","Effect":"Allow","Action":["iam:PutRolePolicy","iam:DeleteRolePolicy","iam:DetachRolePolicy","iam:PutRolePermissionsBoundary"],"Resource":"arn:aws:iam::${a}:role/bioaf-*","Condition":{"StringEquals":{"iam:PermissionsBoundary":"${boundary}"}}},
{"Sid":"RoleManagedPolicies","Effect":"Allow","Action":"iam:AttachRolePolicy","Resource":"arn:aws:iam::${a}:role/bioaf-*","Condition":{"StringEquals":{"iam:PermissionsBoundary":"${boundary}"},"ArnLike":{"iam:PolicyARN":["arn:aws:iam::aws:policy/AmazonEKS*","arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly","arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"]}}},
{"Sid":"RoleLifecycle","Effect":"Allow","Action":["iam:DeleteRole","iam:TagRole","iam:UntagRole","iam:UpdateAssumeRolePolicy","iam:UpdateRole","iam:UpdateRoleDescription"],"Resource":"arn:aws:iam::${a}:role/bioaf-*"},
{"Sid":"PassOwnedRoles","Effect":"Allow","Action":"iam:PassRole","Resource":"arn:aws:iam::${a}:role/bioaf-*","Condition":{"StringEquals":{"iam:PassedToService":["eks.amazonaws.com","ec2.amazonaws.com","codebuild.amazonaws.com"]}}},
{"Sid":"InstanceProfiles","Effect":"Allow","Action":["iam:CreateInstanceProfile","iam:DeleteInstanceProfile","iam:AddRoleToInstanceProfile","iam:RemoveRoleFromInstanceProfile","iam:TagInstanceProfile","iam:UntagInstanceProfile"],"Resource":"arn:aws:iam::${a}:instance-profile/bioaf-*"},
{"Sid":"ClusterIdentity","Effect":"Allow","Action":["iam:CreateOpenIDConnectProvider","iam:DeleteOpenIDConnectProvider","iam:TagOpenIDConnectProvider","iam:UntagOpenIDConnectProvider","iam:UpdateOpenIDConnectProviderThumbprint","iam:AddClientIDToOpenIDConnectProvider"],"Resource":"arn:aws:iam::${a}:oidc-provider/oidc.eks.${r}.amazonaws.com/*"},
{"Sid":"ServiceLinkedRoles","Effect":"Allow","Action":"iam:CreateServiceLinkedRole","Resource":"*","Condition":{"StringEquals":{"iam:AWSServiceName":["eks.amazonaws.com","eks-nodegroup.amazonaws.com","autoscaling.amazonaws.com","elasticloadbalancing.amazonaws.com","spot.amazonaws.com"]}}},
{"Sid":"Images","Effect":"Allow","Action":["ecr:CreateRepository","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:ListImages","ecr:TagResource"],"Resource":"arn:aws:ecr:${r}:${a}:repository/bioaf-*"},
{"Sid":"Builds","Effect":"Allow","Action":["codebuild:CreateProject","codebuild:UpdateProject","codebuild:DeleteProject","codebuild:StartBuild","codebuild:StopBuild"],"Resource":"arn:aws:codebuild:${r}:${a}:project/bioaf-*"},
{"Sid":"BuildLogs","Effect":"Allow","Action":["logs:GetLogEvents","logs:FilterLogEvents"],"Resource":["arn:aws:logs:${r}:${a}:log-group:/aws/codebuild/bioaf-*","arn:aws:logs:${r}:${a}:log-group:/aws/codebuild/bioaf-*:*"]},
{"Sid":"ControlPlaneLogGroup","Effect":"Allow","Action":["logs:CreateLogGroup","logs:PutRetentionPolicy","logs:DeleteRetentionPolicy","logs:TagResource","logs:UntagResource","logs:ListTagsForResource","logs:TagLogGroup","logs:UntagLogGroup","logs:ListTagsLogGroup"],"Resource":["arn:aws:logs:${r}:${a}:log-group:/aws/eks/bioaf-*/cluster","arn:aws:logs:${r}:${a}:log-group:/aws/eks/bioaf-*/cluster:*"]},
{"Sid":"DenySelfAndProtected","Effect":"Deny","Action":["iam:Put*","iam:Delete*","iam:Attach*","iam:Detach*","iam:Update*","iam:Tag*","iam:Untag*","iam:Create*","iam:Add*","iam:Remove*","iam:PassRole"],"Resource":["arn:aws:iam::${a}:role/bioaf-app","arn:aws:iam::${a}:instance-profile/bioaf-app","${boundary}"]},
{"Sid":"DenyProtectedRoles","Effect":"Deny","Action":["iam:Put*","iam:Delete*","iam:Attach*","iam:Detach*","iam:Update*","iam:Tag*","iam:Untag*","iam:PassRole"],"Resource":"*","Condition":{"StringEquals":{"aws:ResourceTag/bioaf:protected":"true"}}},
{"Sid":"DenyBoundaryRemoval","Effect":"Deny","Action":"iam:DeleteRolePermissionsBoundary","Resource":"*"},
{"Sid":"DenyPassUnowned","Effect":"Deny","Action":["iam:PassRole","iam:UpdateAssumeRolePolicy"],"Resource":"*","Condition":{"StringNotEquals":{"aws:ResourceTag/bioaf:installation":"${id}"}}},
{"Sid":"DenyOwnershipUntag","Effect":"Deny","Action":["iam:UntagRole","iam:UntagInstanceProfile"],"Resource":"*","Condition":{"ForAnyValue:StringEquals":{"aws:TagKeys":["bioaf:installation"]}}},
{"Sid":"DenyLogHistoryDelete","Effect":"Deny","Action":"logs:DeleteLogGroup","Resource":"arn:aws:logs:${r}:${a}:log-group:/aws/eks/*"},
{"Sid":"DenyOwnershipRetag","Effect":"Deny","Action":["ec2:CreateTags","ec2:DeleteTags"],"Resource":"*","Condition":{"Null":{"ec2:CreateAction":"true"},"ForAnyValue:StringEquals":{"aws:TagKeys":["bioaf:installation"]}}},
{"Sid":"DenySharing","Effect":"Deny","Action":["ec2:ModifyImageAttribute","ec2:ModifySnapshotAttribute"],"Resource":"*"}
]}
JSON
}

# aws_iam_ensure_boundary ACCOUNT REGION
#     Create the boundary policy, or make the current document its default version (pruning the
#     oldest version when IAM's five-version limit is reached).
aws_iam_ensure_boundary() {
    local a="$1" r="$2" arn doc oldest
    arn="$(aws_iam_boundary_arn "$a")"
    doc="$(mktemp -t bioaf-boundary-XXXXXX)"
    aws_iam_role_boundary_policy "$a" "$r" > "$doc"
    if aws iam get-policy --policy-arn "$arn" >/dev/null 2>&1; then
        oldest="$(aws iam list-policy-versions --policy-arn "$arn" \
            --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text 2>/dev/null | awk '{ if (NF >= 4) print $NF }')"
        [ -z "$oldest" ] || aws iam delete-policy-version --policy-arn "$arn" --version-id "$oldest" || { rm -f "$doc"; return 1; }
        aws iam create-policy-version --policy-arn "$arn" --policy-document "file://${doc}" --set-as-default >/dev/null \
            || { rm -f "$doc"; return 1; }
    else
        aws iam create-policy --policy-name "$(aws_iam_boundary_name)" --policy-document "file://${doc}" \
            --description "bioAF: the most any role the bioAF app creates may do" >/dev/null || { rm -f "$doc"; return 1; }
    fi
    rm -f "$doc"
}

# aws_iam_put_app_policy ACCOUNT REGION APP_VPC_ID ROLE
#     Replace the app role's inline policy with the narrowed document and remove the old starter.
aws_iam_put_app_policy() {
    local a="$1" r="$2" vpc="$3" role="$4" doc
    doc="$(mktemp -t bioaf-app-policy-XXXXXX)"
    aws_iam_app_policy "$a" "$r" "$vpc" > "$doc"
    aws iam put-role-policy --role-name "$role" --policy-name bioaf-app --policy-document "file://${doc}" \
        || { rm -f "$doc"; return 1; }
    rm -f "$doc"
    if aws iam get-role-policy --role-name "$role" --policy-name bioaf-app-starter >/dev/null 2>&1; then
        aws iam delete-role-policy --role-name "$role" --policy-name bioaf-app-starter || return 1
    fi
}

# aws_iam_owned_roles ROLE
#     The roles the app created (bioaf-* tagged managed_by=bioaf), one per line, excluding ROLE.
aws_iam_owned_roles() {
    local app_role="$1" name
    for name in $(aws iam list-roles --query "Roles[?starts_with(RoleName, 'bioaf-')].RoleName" --output text); do
        [ "$name" = "$app_role" ] && continue
        if aws iam list-role-tags --role-name "$name" \
            --query "Tags[?Key=='managed_by' && Value=='bioaf'] | length(@)" --output text | grep -qx '1'; then
            printf '%s\n' "$name"
        fi
    done
}

# aws_iam_owned_ec2 REGION
#     EC2 resource ARNs bioAF created in REGION (managed_by=bioaf or bioaf-managed=true), one per line.
aws_iam_owned_ec2() {
    local r="$1"
    {
        aws resourcegroupstaggingapi get-resources --region "$r" --resource-type-filters ec2 \
            --tag-filters Key=managed_by,Values=bioaf --query 'ResourceTagMappingList[].ResourceARN' --output text
        aws resourcegroupstaggingapi get-resources --region "$r" --resource-type-filters ec2 \
            --tag-filters Key=bioaf-managed,Values=true --query 'ResourceTagMappingList[].ResourceARN' --output text
    } | tr '\t' '\n' | sed '/^$/d; /^None$/d' | sort -u
}

# aws_iam_claim ACCOUNT REGION ROLES_FILE EC2_FILE [INSTANCE_ID]
#     Put the boundary on each listed role and tag it, the EC2 ARNs and the application VM with this
#     installation's ownership tag. Uses the operator's credentials, never the app's.
aws_iam_claim() {
    local a="$1" r="$2" roles="$3" arns="$4" vm="${5:-}" id boundary name batch
    id="$(aws_iam_installation_id "$a" "$r")"
    boundary="$(aws_iam_boundary_arn "$a")"
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        aws iam put-role-permissions-boundary --role-name "$name" --permissions-boundary "$boundary" || return 1
        aws iam tag-role --role-name "$name" --tags "Key=bioaf:installation,Value=${id}" || return 1
    done < "$roles"
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
