// All Uzbek dictionaries. Each section keeps its own file so that work on
// different parts of the interface does not collide; common.js holds the
// shared glossary. Later files never silently replace an earlier translation:
// the dictionary test lists keys translated differently in two files.
import common, {patterns as commonPatterns} from './common.js';
import app, {patterns as appPatterns} from './app.js';
import reports, {patterns as reportsPatterns} from './reports.js';
import businessUi, {patterns as businessUiPatterns} from './business_ui.js';
import businessData, {patterns as businessDataPatterns} from './business_data.js';
import overview, {patterns as overviewPatterns} from './overview.js';
import requests, {patterns as requestsPatterns} from './requests.js';
import admin, {patterns as adminPatterns} from './admin.js';
import server, {patterns as serverPatterns} from './server.js';

export const sources = {common, app, reports, business_ui: businessUi, business_data: businessData, overview, requests, admin, server};
export const entries = Object.assign({}, server, admin, requests, overview, businessData, businessUi, reports, app, common);
export const patterns = [...commonPatterns, ...appPatterns, ...reportsPatterns, ...businessUiPatterns, ...businessDataPatterns,
  ...overviewPatterns, ...requestsPatterns, ...adminPatterns, ...serverPatterns];
