/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

export class FakeModel {
  constructor() {
    this.values = {};
  }

  set(key, value) {
    this.values[key] = value;
  }

  get(key) {
    return this.values[key];
  }

  unset(key) {
    delete this.values[key];
  }

  toJSON() {
    return {...this.values};
  }
}
