import json
import pathlib


def generate_default_corpus(target_dir):
    """
    Programmatically generates the default synthetic support case corpus
    and metadata inside the specified target directory.
    """
    target_path = pathlib.Path(target_dir)
    cases_path = target_path / "cases"
    metadata_path = target_path / "metadata"
    versions_path = metadata_path / "versions"

    # Create directories
    cases_path.mkdir(parents=True, exist_ok=True)
    versions_path.mkdir(parents=True, exist_ok=True)

    # 1. Generate Metadata
    products = [
        {"code": "RHEL", "name": "Red Hat Enterprise Linux"},
        {"code": "OPENSHIFT", "name": "Red Hat OpenShift Container Platform"},
        {"code": "ANSIBLE", "name": "Ansible Automation Platform"},
    ]
    with open(metadata_path / "products.json", "w") as f:
        json.dump(products, f, indent=2)

    severities = [
        {"name": "Low"},
        {"name": "Normal"},
        {"name": "High"},
        {"name": "Urgent"},
    ]
    with open(metadata_path / "severities.json", "w") as f:
        json.dump(severities, f, indent=2)

    case_types = [
        {"name": "Standard"},
        {"name": "Bug"},
    ]
    with open(metadata_path / "case_types.json", "w") as f:
        json.dump(case_types, f, indent=2)

    # Product Versions
    rhel_versions = [
        {"name": "8.4"},
        {"name": "8.8"},
        {"name": "9.0"},
        {"name": "9.2"},
        {"name": "9.4"},
    ]
    with open(versions_path / "RHEL.json", "w") as f:
        json.dump(rhel_versions, f, indent=2)

    ocp_versions = [
        {"name": "4.12"},
        {"name": "4.13"},
        {"name": "4.14"},
        {"name": "4.15"},
        {"name": "4.16"},
    ]
    with open(versions_path / "OPENSHIFT.json", "w") as f:
        json.dump(ocp_versions, f, indent=2)

    ansible_versions = [{"name": "2.2"}, {"name": "2.3"}, {"name": "2.4"}]
    with open(versions_path / "ANSIBLE.json", "w") as f:
        json.dump(ansible_versions, f, indent=2)

    # 2. Generate Cases
    case_1001 = {
        "caseNumber": "1001",
        "summary": "Cannot mount NFS share on RHEL 9.0",
        "description": "When executing 'mount -t nfs 10.0.1.5:/export /mnt/nfs', we receive a 'Permission Denied' error. The NFS server exports configuration seems correct and allows our client IP. System logs show RPC connection timeout.",
        "product": "RHEL",
        "version": "9.0",
        "accountNumber": "123456",
        "status": "Waiting on Red Hat",
        "severity": "High",
        "caseType": "Standard",
        "createdDate": "2026-07-10T14:30:00Z",
        "contact": {"name": "Jane Developer", "ssoUsername": "jdeveloper"},
        "owner": {"name": "Red Hat Support Specialist", "ssoUsername": "rh_spec"},
        "lastModifiedBy": "Red Hat Support Specialist",
        "lastModifiedDate": "2026-07-11T09:15:00Z",
        "comments": [
            {
                "id": "c1",
                "createdDate": "2026-07-10T15:00:00Z",
                "createdBy": "Red Hat Support Specialist",
                "isPublic": True,
                "body": "Hi Jane,\n\nCould you please share the output of 'showmount -e 10.0.1.5' from the client, and attach your client-side /var/log/messages? Also verify if firewalld or any network security groups are blocking TCP/UDP ports 111 and 2049.",
            },
            {
                "id": "c2",
                "createdDate": "2026-07-10T16:22:00Z",
                "createdBy": "Jane Developer",
                "isPublic": True,
                "body": "Hi,\n\nI have attached the requested logs. 'showmount -e' succeeds and shows the export. I verified that port 2049 is open using nc.",
            },
        ],
        "attachments": [
            {
                "id": "a1",
                "name": "client_nfs_messages.log",
                "size": 15420,
                "createdDate": "2026-07-10T16:20:00Z",
            }
        ],
        "notifiedusers": [{"ssoUsername": "team_lead"}],
    }

    case_1002 = {
        "caseNumber": "1002",
        "summary": "OpenShift Registry push auth failure after cluster upgrade",
        "description": "After upgrading our OpenShift Container Platform cluster from 4.14 to 4.15, builds fail when pushing images to the internal registry. We receive 'x509: certificate signed by unknown authority' errors in the build logs.",
        "product": "OPENSHIFT",
        "version": "4.15",
        "accountNumber": "123456",
        "status": "Waiting on Customer",
        "severity": "Urgent",
        "caseType": "Bug",
        "createdDate": "2026-07-11T08:00:00Z",
        "contact": {"name": "Bob Admin", "ssoUsername": "badmin"},
        "owner": {"name": "Red Hat OpenShift Engineer", "ssoUsername": "ocp_eng"},
        "lastModifiedBy": "Red Hat OpenShift Engineer",
        "lastModifiedDate": "2026-07-11T11:45:00Z",
        "comments": [
            {
                "id": "c1",
                "createdDate": "2026-07-11T09:30:00Z",
                "createdBy": "Red Hat OpenShift Engineer",
                "isPublic": True,
                "body": "Hello Bob,\n\nThis usually occurs if the cluster-ingress CA certificate was not automatically trusted by the internal registry or if custom certificates are in use. Could you please run the following command and share the output:\n\noc get image.config.openshift.io/cluster -o yaml",
            }
        ],
        "attachments": [],
        "notifiedusers": [],
    }

    case_1003 = {
        "caseNumber": "1003",
        "summary": "Ansible automation controller job timeout in large inventories",
        "description": "We are experiencing severe job timeouts (exceeding 2 hours) on inventory syncs for large AWS environments (~10,000 instances). The same sync took 15 minutes before upgrading Ansible Automation Platform to 2.4.",
        "product": "ANSIBLE",
        "version": "2.4",
        "accountNumber": "123456",
        "status": "Closed",
        "severity": "Normal",
        "caseType": "Standard",
        "createdDate": "2026-06-15T10:00:00Z",
        "contact": {"name": "Jane Developer", "ssoUsername": "jdeveloper"},
        "owner": {"name": "Ansible Support Engineer", "ssoUsername": "ansible_eng"},
        "lastModifiedBy": "System",
        "lastModifiedDate": "2026-06-20T18:00:00Z",
        "comments": [
            {
                "id": "c1",
                "createdDate": "2026-06-15T11:00:00Z",
                "createdBy": "Ansible Support Engineer",
                "isPublic": True,
                "body": "Hi Jane,\n\nIn AAP 2.4, the default inventory plugin settings changed to execute serial batch chunking. Try adding the environment variable 'ANSIBLE_INVENTORY_AWS_BATCH_SIZE=500' to the execution environment or controller settings to optimize performance.",
            },
            {
                "id": "c2",
                "createdDate": "2026-06-16T09:15:00Z",
                "createdBy": "Jane Developer",
                "isPublic": True,
                "body": "Setting 'ANSIBLE_INVENTORY_AWS_BATCH_SIZE=500' solved the issue immediately! Sync time is now down to 12 minutes. You can close this ticket.",
            },
            {
                "id": "c3",
                "createdDate": "2026-06-16T10:00:00Z",
                "createdBy": "Ansible Support Engineer",
                "isPublic": True,
                "body": "Excellent! I will proceed to close this case. Let us know if you need anything else.",
            },
        ],
        "attachments": [],
        "notifiedusers": [],
    }

    with open(cases_path / "1001.json", "w") as f:
        json.dump(case_1001, f, indent=2)

    with open(cases_path / "1002.json", "w") as f:
        json.dump(case_1002, f, indent=2)

    with open(cases_path / "1003.json", "w") as f:
        json.dump(case_1003, f, indent=2)
