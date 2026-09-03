//////////////////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////////////////

const profiles = [
  {
    profile_id: 'postgresql-native', display_name: 'PostgreSQL',
    workflow: 'legacy_preserved', route_kind: 'network',
    default_port: 5432, default: true,
  },
  {
    profile_id: 'qualified-native', display_name: 'Qualified engine',
    workflow: 'provider_endpoint', route_kind: 'network',
    default_port: 1234, default: false,
    connection_fields: [{
      field_id: 'tls_mode', route_key: 'tls_mode', label: 'TLS mode',
      control: 'select', group: 'Qualified TLS', default: 'disabled',
      options: [
        {value: 'disabled', label: 'Disabled'},
        {value: 'system-ca', label: 'System CA validation'},
      ],
    }],
  },
  {
    profile_id: 'embedded-native', display_name: 'Embedded engine',
    workflow: 'provider_endpoint', route_kind: 'embedded_file',
    default_port: null, default: false,
  },
];

const endpointProfiles = {
  profiles,
  defaultProfile: profiles[0],
  options: profiles.map((profile) => ({
    label: profile.display_name, value: profile.profile_id,
  })),
  get: (profileId) => profiles.find(
    (profile) => profile.profile_id === profileId
  ),
};

module.exports = endpointProfiles;
