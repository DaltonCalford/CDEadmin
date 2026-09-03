# CDEadmin Security Policy

CDEadmin is an independent hard fork of pgAdmin 4 9.17. It is not affiliated
with or endorsed by the pgAdmin Development Team. Do not send CDEadmin security
reports to pgAdmin security or support channels.

## Supported Versions

No CDEadmin version is currently approved as a stable production release.
Supported-version and security-maintenance commitments will be published before
the first approved release.


## Reporting a Vulnerability

The CDEadmin project-owned private security contact is not yet assigned. Until
it is, record findings only in the authorized private development workspace or
private review channel; do not place undisclosed vulnerabilities in a public
issue tracker. This missing channel is a release blocker.

### **What to Include in Your Report**
To help us quickly understand and address the issue, please include the following sections in your report:

#### 1. **Summary**
   - A brief description of the vulnerability.

#### 2. **Affected Versions**
   - The version(s) of the project affected by the vulnerability.
   - Example: "Affects versions 3.4.0 to 3.6.23."

#### 3. **Details**
   - A detailed explanation of the vulnerability, including:
     - How to reproduce the issue (step-by-step instructions).
     - The code or component where the vulnerability exists.
     - The expected vs. actual behavior.

#### 4. **Proof of Concept (PoC)**
   - Provide a proof of concept to demonstrate the vulnerability. This could be:
     - Code snippets.
     - Screenshots or videos.
     - A minimal reproducible example.

#### 5. **Patches (if applicable)**
   - If you have a suggested fix or patch, include it in your report.
   - Example: "Sanitize user input using `DOMPurify`."

#### 6. **Impact**
   - Describe the potential impact of the vulnerability, such as:
     - Remote Code Execution.
     - CSRF.
     - Data exposure.
     - Denial of service.



### **What to Expect**

Response-time, update and CVE-coordination commitments are not yet published.
They must be established with the project-owned security channel before a
production release is approved.


### **Out of Scope**
The following issues are considered out of scope for security reports:
- Vulnerabilities in outdated or unsupported versions.
- Issues related to non-security-impacting bugs or feature requests.
- Vulnerabilities requiring physical access to the device or social engineering.



## Security Updates

We are committed to providing timely security updates for supported versions. Here’s our process:
1. **Assessment**:
   - All reported vulnerabilities are assessed for severity and impact.
2. **Patch Development**:
   - Patches are developed and tested in a private repository to prevent premature disclosure.
3. **Release**:
   - Security patches are released as soon as possible, along with a detailed advisory.



## Acknowledgments

We deeply appreciate the efforts of security researchers and users who help us improve the security of our project.



## Contact

The CDEadmin security contact is unassigned. The upstream
`security@pgadmin.org` address is not a CDEadmin contact.
