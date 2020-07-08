import * as Comlink from 'comlink';
import * as d3 from 'd3';
import { formatTime, addHours, inFieldOfView } from './util';
import { Long, serialize, deserialize } from 'bson';
import * as models from './models';


declare const GENERATE_MOCKING: boolean;

let obj: models.Sig;

if (GENERATE_MOCKING) {
  let data = null;
  const mocking = require('./mocking');

  obj = {
    type: 'mocking',
    now: null,
    data,
    async initializeData(date = new Date()) {
      if (obj.type == 'mocking') {
        obj.now = date;
      }
      data = mocking.generateData(date);
    },
    async getShiftsInRange([minDate, maxDate]: models.DateRange): Promise<models.ShiftsResponse> {
      if (data == null) throw new Error('data not initialized');
      const { shifts, employees } = data;
      const filteredShifts: models.Shift[] = [];
      const filteredEmployees = {};
      const employeeIds = [];
      for (const shift of shifts) {
        if (inFieldOfView([shift.start, shift.end], [minDate, maxDate])) {
          const employeeId = shift.employee;
          if (!employeeIds.includes(employeeId)) {
            filteredEmployees[employeeId] = employees[employeeId];
            employeeIds.push(employeeId);
          }
          filteredShifts.push(shift);
        }
      }
      return { shifts: filteredShifts, employees: filteredEmployees, employeeIds, range: [minDate, maxDate] };
    },
    async getShiftsByEmployeeInRange(employeeId: models.EmployeeID, [minDate, maxDate]: models.DateRange): Promise<models.ShiftsResponse> {
      if (data == null) throw new Error('data not initialized');
      const { shifts, employees } = data;
      const employee = employees[employeeId];
      const filteredShifts: models.Shift[] = [];
      for (const shift of shifts) {
        if (shift.employee == employeeId && inFieldOfView([shift.start, shift.end], [minDate, maxDate])) {
          filteredShifts.push(shift);
        }
      }
      return {range: [minDate, maxDate], shifts: filteredShifts, employees: {[employeeId]: employee}, employeeIds: [employeeId]};
    },
    async getGraphData(dateRange: models.DateRange): Promise<models.GraphDataResponse> {
      const {shifts, employees} = data as {shifts: models.Shift[], employees: {[id: string]: models.Employee}};
      const employeesList = [];
      const employeeIds = [];
      for (const employeeId in employees) {
        employeeIds.push(employeeId);
        employeesList.push(employees[employeeId]);
      }
      const now = obj.type == 'mocking' ? obj.now : new Date();
      const l = 48;
      const columns = Array.from(Array(l)).map((_, i) => i.toString());
      const graphData = columns
        .map(_id => ({_id, total: 0, buckets: employeeIds.reduce((acc, id) => ({...acc, [id]: 0}), {})}))
        .sort((a, b) => b.total - a.total);
      for (const shift of shifts) {
        for (const component of shift.components) {
          let start, end;
          ({start, end} = component);
          end = end || now;
          ([start, end] = [start, end].map(d => d.getHours() * 60 * 60 + d.getMinutes() * 60 + d.getSeconds()));
          if (start > end) {
            end += 24 * 60 * 60;
          }
          ([start, end] = [start, end].map(g => Math.floor(g / 1800)));
          for (let i = start; i < end; i++) {
            graphData[i % l].buckets[shift.employee.id] += 1;
            graphData[i % l].total += 1;
          }
        }
      }
      return {employees: employeesList, data: graphData, columns};
    },
    async getWeeklyGraphData([minDate, maxDate]: models.DateRange) {
      const now = obj.type == 'mocking' ? obj.now : new Date();
      const {shifts, employees} = data as {shifts: models.Shift[], employees: {[id: string]: models.Employee}};
      const [minWeek, maxWeek] = [d3.timeWeek.floor(minDate), d3.timeWeek.offset(d3.timeWeek.floor(maxDate), 1)];

      const filteredShifts = shifts.filter(d => (d.start > minWeek && d.start < maxWeek)
        || (d.end > minWeek && d.end < maxWeek)
        || (d.end < minWeek && d.start > maxWeek)).sort((a, b) => a.start > b.start ? 1 : -1);

      const buckets = [];
      const active = [];
      for (const date of d3.timeMinute.every(30).range(minWeek, maxWeek)) {
        for (let j = 0; j < active.length;) {
          if (active[j].end < date) {
            active.splice(j, 1);
          }
          j++;
        }
        for (let i = 0; i < filteredShifts.length;) {
          const shift = filteredShifts[i];
          if (shift.start < date) {
            const arr = filteredShifts.splice(i, 1);
            if (shift.end > date) {
              active.push(...arr);
            }
          }
          i++;
        }
        buckets.push({date, count: active.length}); // could also include active items
      }
      return buckets;
    }
  };
} else {
  obj = {
    type: 'fetch',
    async getShiftsInRange([minDate, maxDate]: models.DateRange) {
      const url = new URL(`/data/shifts`, location.origin);
      url.searchParams.set('minDate', minDate.toISOString());
      url.searchParams.set('maxDate', maxDate.toISOString());
      const res = await fetch(url.toString(), {headers: {'Accept': 'application/bson'}});
      if (res.status < 200 || res.status >= 400) {
        throw new Error(`failed to fetch shifts: ${res.statusText}`);
      }
      let content;
      if (res.headers.has('Content-Type') && res.headers.get('Content-Type') === 'application/bson') {
        let buf = await res.arrayBuffer();
        buf = new Uint8Array(buf)
        content = deserialize(buf);
      } else {
        content = await res.json();
        interpretResponse(content);
      }
      return content;
    },
    async getShiftsByEmployeeInRange(employeeId: models.EmployeeID, [minDate, maxDate]: models.DateRange) {
      const url = new URL(`/data/shifts`, location.origin);
      url.searchParams.set('minDate', minDate.toISOString());
      url.searchParams.set('maxDate', maxDate.toISOString());
      url.searchParams.set('employee', employeeId);
      const res = await fetch(url.toString(), {headers: {'Accept': 'application/bson'}});
      if (res.status < 200 || res.status >= 400) {
        throw new Error(`failed to fetch shifts: ${res.statusText}`);
      }
      let content;
      if (res.headers.has('Content-Type') && res.headers.get('Content-Type') === 'application/bson') {
        let buf = await res.arrayBuffer();
        buf = new Uint8Array(buf)
        content = deserialize(buf);
      } else {
        content = await res.json();
        interpretResponse(content);
      }
      return content;
    },
    async getGraphData(dateRange?: models.DateRange): Promise<models.GraphDataResponse> {
      const url = new URL(`/data/graph`, location.origin);
      const res = await fetch(url.toString())
      if (res.status < 200 || res.status >= 400) {
        throw new Error(`failed to fetch shifts: ${res.statusText}`);
      }
      const content = await res.json();
      let {data, employees, columns} = content;
      data = data.sort((a, b) => b.total - a.total);
      return {data, employees, columns};
    },
    async getWeeklyGraphData([minDate, maxDate]: models.DateRange) {
      const url = new URL(`/data/weekly`, location.origin);
      url.searchParams.set('minDate', minDate.toISOString());
      url.searchParams.set('maxDate', maxDate.toISOString());
      const res = await fetch(url.toString(), {headers: {'Accept': 'application/bson'}});
      if (res.status < 200 || res.status >= 400) {
        throw new Error(`failed to fetch weekly graph data: ${res.statusText}`);
      }
      let content;
      if (res.headers.has('Content-Type') && res.headers.get('Content-Type') === 'application/bson') {
        let buf = await res.arrayBuffer();
        buf = new Uint8Array(buf)
        content = deserialize(buf);
      } else {
        content = await res.json();
      }
      if (!('data' in content)) {
        throw new Error('invalid weekly graph data response');
      }
      return content.data;
    },
  }
}

async function get(key) {
  const res = await fetch(`data/${key}.json`);
  const json = await res.json();
  return json;
}

/*
Object.assign(obj, {
  data: {
    shifts: get('shifts').then(arr => {
      for (const each of arr) {
        each['start'] = new Date(each['start']);
        each['end'] = each['end'] != null ? new Date(each['end']) : null;
      }
      return arr;
    }),
    employees: get('employees').then(arr => {
      return Object.values(arr).reduce((acc, {id, Code, Name, MiddleName, LastName}) => {
        acc[id] = {id, name: {first: Name, last: LastName}};
        return acc;
      }, {});
    }),
  },
  async getEmployees() {
    return Object.values(await obj.data.employees);
  },
  async getEmployeeShifts(ids: number[], [fromDate, toDate]): Promise<{[id: string]: any[]}> {
    const shifts = await obj.data.shifts;
    const subset = ids.reduce((acc, id) => ({...acc, [id]: []}), {});
    for (const shift of shifts) {
      if (ids.includes(shift.employee)) {
        subset[shift.employee].push(shift);
      }
    }
    return subset;
  },
  async getData([fromDate, toDate]) {
    const allShifts = await obj.data.shifts;
    const allEmployees = await obj.data.employees;
    const shifts = [];
    const employees = {};
    const employeeIds = []
    for (const shift of allShifts) {
      if (inFieldOfView([shift.start, shift.end], [fromDate, toDate])) {
        const employeeId = shift.employee;
        const employee = allEmployees[employeeId];
        if (!employeeIds.includes(shift.employee)) {
          employeeIds.push(employeeId);
          employees[employeeId] = employee;
        }
        let state, typicalEnd = null;
        if (shift.end != null) {
          state = ShiftState.Complete;
        } else {
          // made up bs
          typicalEnd = new Date(shift.start);
          typicalEnd.setHours(typicalEnd.getHours() + 8);
          state = ShiftState.Incomplete;
        }
        shifts.push({
          id: shift.id,
          employee,
          shift: {state, actual: {start: shift.start, end: shift.end}, typical: {start: null, end: typicalEnd}},
          pos: {x: 0, y: 0, w: 0, x1: 0, w1: 0, yi: 0},
          display: {
            center: employee.name.first + ' ' + employee.name.last,
            left: formatTime(shift.start),
            right: formatTime(state === ShiftState.Complete ? shift.end : typicalEnd),
          },
        });
      }
    }
    return {employeeIds, employees, shifts};
  }
});
*/

function sortBy(arr) {
  return function (a, b) {
    for (const key of arr) {
      if (a[key] > b[key]) return 1;
      if (a[key] < b[key]) return -1;
    }
    return 0;
  }
}

function interpretResponse(content) {
  const {shifts, employees, employeeIds} = content;
  const employeeColors = {};
  for (const empl of Object.values(employees) as any[]) {
    if (empl['_id'] != null && '$oid' in empl['_id']) {
      empl['_id'] = empl['_id']['$oid'];
    }
    employeeColors[empl['id']] = empl['Color']
    if (empl.HireDate != null && '$date' in empl.HireDate) {
      empl.HireDate = new Date(empl.HireDate['$date']);
    } else if (typeof empl.HireDate === 'string') {
      empl.HireDate = new Date(empl.HireDate);
    }
    if (empl.stats) {
      for (const [key, value] of Object.entries(empl.stats)) {
        if (value != null && value['$date']) {
          empl.stats[key] = new Date(value['$date']);
        }
      }
    }
  }
  for (const shift of shifts) {
    shift.id = shift._id;
    if (isNaN(shift.row)) {
      shift.row = 18;
    }
    if (shift['_id'] != null && '$oid' in shift['_id']) {
      shift['_id'] = shift['_id']['$oid'];
      shift['id'] = shift['_id']
    }
    shift['employeeColor'] = employeeColors[shift['employee']];
    shift.started = true;
    if (typeof shift.expectedDuration !== "number") {
      shift.expectedDuration = 2.88e7;
    }
    if (shift.start != null && '$date' in shift.start) {
      shift.start = new Date(shift.start['$date']);
    } else if (typeof shift.start === 'string') {
      shift.start = new Date(shift.start);
    }
    if (shift.end != null && '$date' in shift.end) {
      shift.end = new Date(shift.end['$date']);
    } else if (typeof shift.end === 'string') {
      shift.end = new Date(shift.end);
    }
    if (Array.isArray(shift.components)) {
      for (const comp of shift.components) {
        if (comp['_id'] != null && '$oid' in comp['_id']) {
          comp['_id'] = comp['_id']['$oid'];
          comp['id'] = comp['_id']
        }
        comp.showTime = true;
        if (comp.start != null && '$date' in comp.start) {
          comp.start = new Date(comp.start['$date']);
        } else if (typeof comp.start === 'string') {
          comp.start = new Date(comp.start);
        }
        if (comp.end != null && '$date' in comp.end) {
          comp.end = new Date(comp.end['$date']);
        } else if (typeof comp.end === 'string') {
          comp.end = new Date(comp.end);
        } else if (comp.end == null) {
          comp.end = new Date();
        }
      }
    }
  }
}

Comlink.expose(obj, self as any);
