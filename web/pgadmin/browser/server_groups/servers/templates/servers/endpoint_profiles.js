/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

define(
  'pgadmin.cdeadmin.endpoint_profiles',
  [],
  function() {
    const profiles = {{ profiles | tojson }};
    const defaultProfile = profiles.find((profile) => profile.default);
    return {
      profiles,
      defaultProfile,
      options: profiles.map((profile) => ({
        label: profile.display_name,
        value: profile.profile_id,
        engineId: profile.engine_id,
        interfaceId: profile.interface_id,
        protocolId: profile.protocol_id,
      })),
      interfaces: (engineId) => profiles.filter(
        (profile) => profile.engine_id === engineId
      ),
      get: (profileId) => profiles.find(
        (profile) => profile.profile_id === profileId
      ),
    };
  }
);
