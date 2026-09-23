const fs = require('fs');
const medical = db.getSiblingDB('p05_medical');

function secret(name) {
  const value = fs.readFileSync(`/run/secrets/${name}`, 'utf8').trim();
  if (!value) throw new Error(`Empty secret: ${name}`);
  return value;
}

if (!medical.getUser('p05_ingest')) {
  medical.createUser({
    user: 'p05_ingest',
    pwd: secret('mongo_ingest_password'),
    roles: [{ role: 'readWrite', db: 'p05_medical' }],
  });
}

if (!medical.getUser('p05_reader')) {
  medical.createUser({
    user: 'p05_reader',
    pwd: secret('mongo_reader_password'),
    roles: [{ role: 'read', db: 'p05_medical' }],
  });
}

// Normalize the initial root secret consistently with the application secrets.
const admin = db.getSiblingDB('admin');
if (admin.getUser('p05_admin')) {
  admin.updateUser('p05_admin', { pwd: secret('mongo_root_password') });
} else {
  admin.createUser({ user: 'p05_admin', pwd: secret('mongo_root_password'),
    roles: [{ role: 'root', db: 'admin' }] });
}

medical.admissions.createIndex(
  { source_sha256: 1, source_row: 1 },
  { unique: true, name: 'ux_source_row' }
);
medical.admissions.createIndex({ admission_date: 1 }, { name: 'ix_admission_date' });
medical.admissions.createIndex(
  { medical_condition: 1, admission_date: 1 },
  { name: 'ix_condition_admission' }
);
print('P05 application roles and indexes initialized');
