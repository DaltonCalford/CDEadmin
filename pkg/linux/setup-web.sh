#!/bin/bash

#
# Set up CDEadmin in server mode.
#

if [ "$EUID" -ne 0 ]
  then echo "This script must be run as root"
  exit 1
fi

if [[ "$#" -gt 1 ]] || { [[ "$#" -eq 1 ]] && [[ "$1" != "--yes" ]]; }; then
    echo "Usage: $0 [--yes]"
    exit 1
fi

IS_REDHAT=0
IS_DEBIAN=0
IS_SUSE=0
UNAME=$(uname -a)

# Get the distro from the environment
if [ "${CDEADMIN_PLATFORM_TYPE:-${PGADMIN_PLATFORM_TYPE:-}}" == "" ]; then
    if [ -f /etc/redhat-release ]; then
        PLATFORM_TYPE=redhat
    elif [[ ${UNAME} =~ "Ubuntu" ]] || [[ ${UNAME} =~ "Debian" ]] || [ -f /etc/apt/sources.list ]; then
        PLATFORM_TYPE=debian
    elif [ -f /etc/os-release ]; then
        if grep suse /etc/os-release > /dev/null
        then
            PLATFORM_TYPE=suse
        fi
    else
        echo "Failed to detect the platform. This Linux distribution may not be supported by CDEadmin."
        echo "Set CDEADMIN_PLATFORM_TYPE to 'redhat' or 'debian' and try again."
        exit 1
    fi
else
    PLATFORM_TYPE=${CDEADMIN_PLATFORM_TYPE:-${PGADMIN_PLATFORM_TYPE}}
fi

case ${PLATFORM_TYPE} in
    redhat)
        echo "Setting up CDEadmin in web mode on a Red Hat based platform..."
        IS_REDHAT=1
        APACHE=httpd
        ;;

    debian)
        echo "Setting up CDEadmin in web mode on a Debian based platform..."
        IS_DEBIAN=1
        APACHE=apache2
        ;;
    suse)
        echo "Setting up CDEadmin in web mode on a SUSE based platform..."
        IS_SUSE=1
        APACHE=apache2
        ;;

    *)
        echo "Invalid CDEADMIN_PLATFORM_TYPE. Set it to 'redhat', 'debian', or 'suse'."
        exit 1
        ;;
esac

# Is this an automated install?
AUTOMATED=0
if [ "$#" -eq 1 ]; then
    AUTOMATED=1
    echo "Running in non-interactive mode..."
fi

# Run setup script first:
echo "Creating configuration database..."
if ! /usr/cdeadmin/venv/bin/python3 /usr/cdeadmin/web/setup.py setup-db;
then
	echo "Error setting up server mode. Please examine the output above."
	exit 1
fi

# Create and own directories:
echo "Creating storage and log directories..."
mkdir -p /var/log/cdeadmin /var/lib/cdeadmin

if [ ${IS_REDHAT} == 1 ]; then
    chown apache: /var/log/cdeadmin /var/lib/cdeadmin -R
elif [ ${IS_SUSE} == 1 ]; then
    chown wwwrun: /var/log/cdeadmin /var/lib/cdeadmin -R
else
    chown www-data: /var/log/cdeadmin /var/lib/cdeadmin -R
fi

# Set SELinux up:
if [ ${IS_REDHAT} == 1 ]; then
    echo "Configuring SELinux..."
    setsebool -P httpd_tmp_exec 1 1> /dev/null
    setsebool -P httpd_can_network_connect 1 1> /dev/null
    setsebool -P httpd_can_network_connect_db 1 1> /dev/null
    semanage fcontext -a -t httpd_var_lib_t '/var/lib/cdeadmin(/.*)?' 1> /dev/null
    restorecon -R -v /var/lib/cdeadmin 1> /dev/null
    semanage fcontext -a -t httpd_log_t '/var/log/cdeadmin(/.*)?' 1> /dev/null
    restorecon -R -v /var/log/cdeadmin 1> /dev/null
fi

# Setup Apache on Debian/Ubuntu
if [ ${IS_DEBIAN} == 1 ]; then
    if [ ${AUTOMATED} == 1 ]; then
	      RESPONSE=Y
    else
        read -r -p "Configure Apache and mount CDEadmin at /cdeadmin (y/n)? " RESPONSE
    fi

    case ${RESPONSE} in
        y|Y )
            /usr/sbin/a2enmod wsgi 1> /dev/null
            /usr/sbin/a2enconf cdeadmin 1> /dev/null
            ;;
        * )
            exit 1;;
    esac
fi

if pgrep ${APACHE} > /dev/null; then
    if [ ${AUTOMATED} == 1 ]; then
        RESPONSE=Y
    else
        read -r -p "Apache must be restarted to complete the CDEadmin installation. Continue (y/n)? " RESPONSE
    fi

    case ${RESPONSE} in
        y|Y )
            COMMAND=""
            if [ -x "$(command -v systemctl)" ]; then
                COMMAND="systemctl restart ${APACHE}"
            elif [ -x "$(command -v service)" ]; then
                COMMAND="service ${APACHE} restart"
            fi

            if ! ${COMMAND}; then
                echo "Error restarting ${APACHE}. Please check the systemd logs"
            else
                echo "Apache successfully restarted. CDEadmin is available at http://127.0.0.1/cdeadmin"
            fi;;
        * ) 
            exit 1;;
    esac
else
    if [ ${AUTOMATED} == 1 ]; then
        RESPONSE=Y
    else
        read -r -p "Apache is not running. Enable and start it to finish CDEadmin installation (y/n)? " RESPONSE
    fi

    case ${RESPONSE} in
        y|Y )
            if ! systemctl enable ${APACHE}; then
                echo "Error enabling ${APACHE}. Please check the systemd logs"
            else
                echo "Apache successfully enabled."
            fi

            if ! systemctl start ${APACHE}; then
                echo "Error starting ${APACHE}. Please check the systemd logs"
            else
                echo "Apache successfully started."
                echo "CDEadmin is available at http://127.0.0.1/cdeadmin"
            fi;;
        * ) 
            exit 1;;
    esac
fi

exit 0
