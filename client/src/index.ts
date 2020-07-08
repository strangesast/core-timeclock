import './index.scss';

declare const GENERATE_MOCKING: boolean;
declare const NODE_ENV: string;

import * as d3 from 'd3';
import { group } from 'd3-array';
import { from, empty, concat, of, fromEvent, Subject } from 'rxjs';
import * as Comlink from 'comlink';
import {
  exhaustMap,
  pluck,
  delay,
  filter,
  shareReplay,
  share,
  startWith,
  switchMap,
  tap,
  map,
  throttleTime,
  withLatestFrom,
} from 'rxjs/operators';
import { socket } from './socket';

import { Employee, ShiftComponent, Shift, ShiftComponentType, ShiftState } from './models';
import { formatDuration, formatTime, formatDateWeekday } from './util';
import * as constants from './constants';

import { ApolloClient } from 'apollo-client';
import { InMemoryCache } from 'apollo-cache-inmemory';
import { SubscriptionClient } from 'subscriptions-transport-ws';
import { WebSocketLink } from 'apollo-link-ws';
import gql from 'graphql-tag';

const query = gql`
query MyQuery($fromDate: timestamp, $toDate: timestamp) {
  shifts: timeclock_shifts_view(where: {
    _or: [
      {_and: {date_start: {_lt: $toDate}, date_stop: {_gte: $fromDate}}},
      {_and: {date_start: {_lte: $fromDate}, date_stop: {_gt: $toDate}}},
    ]}) {
    date
    date_start
    date_stop
    duration
    employee {
      code
      color
      first_name
      last_name
      middle_name
      id
    }
    row
    segments
    shift_ids
    shift_num
  }
}
`;
const uri = '/v1/graphql';
const link = new WebSocketLink(new SubscriptionClient(`ws://${window.location.host}${uri}`, { reconnect: true }));
const client = new ApolloClient({link, cache: new InMemoryCache()});

function parseResponse({date, date_start, date_stop, duration, employee, row, segments, shift_ids, shift_num}) {
  segments = segments.map(t => t.map(parseDate)).sort((a, b) => d3.ascending(a[0], b[0]));
  duration = segments.reduce((acc, [a, b]) => acc + ((+b || +new Date()) - +a), 0);
  return {
    id: [employee.id, shift_num].join('-'),
    date: new Date(...date.split('-').map(Number) as [number, number, number]),
    start: date_start && new Date(date_start + 'Z'),
    end: date_stop && new Date(date_stop + 'Z'),
    employee,
    components: segments.map(([start, end]) => ({start, end})),
    duration,
    expectedDuration: 8 * 3.6e6,
    shift_ids,
    shift_num,
    row: row - 1,
  };
}

function parseDate(s) {
  return s && new Date(s + 'Z');
}

const toDate = new Date();
const fromDate = d3.timeDay.offset(toDate, -1);

function getShiftsInRange([fromDate, toDate]: Date[]) {
  const variables = {fromDate, toDate};
  
  //from(client.watchQuery({query, variables})).pipe(
  return from(client.query({query, variables})).pipe(
    pluck('data', 'shifts'),
    map(data => {
      const shifts = data.map(parseResponse);
      return {shifts};
    }),
  );
}

const worker = Comlink.wrap(new Worker('./worker.js', { type: 'module' })) as any;

let now: Date;

async function main() {
  if (GENERATE_MOCKING) {
    now = d3.timeWeek.floor(new Date());
    now.setDate(now.getDate() + 3);
    now.setHours(14, 22, 0, 0);
  
    await worker.initializeData(now);
  } else {
    now = new Date();
  }
  await byTime(now);
}

const body = d3.select(document.body);
const svg = body.append('svg');
const svgElement = svg.node() as SVGElement;

const gShifts = svg.append('g').classed('shifts', true);

async function byTime(date: Date) {
  const margin = {
    top: 80,
    left: 10,
    right: 10,
    bottom: 10,
  };

  const {
    innerWidth: width,
    innerHeight: height,
  } = window;

  const axisLabelHeight = 30;
  const resolution = 30 / 3600000; // 30 pixels per hour

  const domainWidth = width / resolution;

  // TODO: use fixed width (2x screen height or ...)

  // const targetDomainWidth = 20 * 7 * 24 * 60 * 60 * 1000; // two weeks
  // const totalWidth = targetDomainWidth * resolution;

  const totalWidth = width * 20; // set svg element size

  const rowCount = 14;
  const contentHeight = rowCount * constants.ROW_STEP + margin.top + margin.bottom + axisLabelHeight;

  const xScale = d3.scaleTime();

  const resetScale = (centerDate) => {
    const d = +centerDate;
    const domain = [
      new Date(d - domainWidth / 2),
      new Date(d + domainWidth / 2),
    ];
    const range = [
      totalWidth / 2 - width / 2,
      totalWidth / 2 + width / 2,
    ]
    xScale.range(range).domain(domain);
    const fullRange = [0, totalWidth];
    const fullDomain = [xScale.invert(0), xScale.invert(totalWidth)];
    xScale.range(fullRange).domain(fullDomain);
  };

  resetScale(date);

  drawTopAxis(svg, xScale, contentHeight - margin.bottom - margin.top - axisLabelHeight);
  drawBottomAxis(svg, xScale, contentHeight - axisLabelHeight - margin.bottom);
  drawDateAxis(svg, xScale);

  const onReset = () => {
    window.scrollTo({...args, left: xScale(now) - width / 2, behavior: 'smooth'});
  }

  svg.attr('width', totalWidth).attr('height', height);

  const windowDim$ = fromEvent(window, 'resize').pipe(
    startWith(null),
    map(() => {
      const {innerWidth, innerHeight} = window;
      return {innerWidth, innerHeight};
    }),
    shareReplay(1),
  );

  const dim$ = windowDim$.pipe(
    map(({innerWidth}) => ({width: totalWidth, height: height})),
    shareReplay(1),
  );

  const scrollPos$ = fromEvent(window, 'scroll').pipe(
    map(() => {
      const {scrollX, scrollY} = window;
      return {scrollX, scrollY};
    }),
    shareReplay(1),
  );

  const scrollEdgeThreshold = 2;

  const sub0 = scrollPos$.pipe(
    withLatestFrom(dim$, windowDim$, ({scrollX}, {width}, {innerWidth}) => ({scrollX, width, innerWidth})),
    map(({innerWidth, width, scrollX}) => (scrollX < scrollEdgeThreshold)
      || ((width - (scrollX + innerWidth)) < scrollEdgeThreshold) ? scrollX : null),
    filter(val => val !== null),
    exhaustMap(v => concat(
      of(v),
      empty().pipe(delay(200)), // TODO: use position threshold instead of time
    )),
    tap(pos => {
      // const newCenterDate = xScale.domain()[dir == -1 ? 0 : 1];
      const newCenterDate = xScale.invert(pos);

      resetScale(newCenterDate);
      drawTopAxis(svg, xScale, contentHeight - margin.bottom - margin.top - axisLabelHeight);
      drawBottomAxis(svg, xScale, contentHeight - axisLabelHeight - margin.bottom);
      drawDateAxis(svg, xScale);

      gShifts.selectAll<SVGGraphicsElement, Shift>('g.shift')
        .each(d => {
          const [a, b] = [d.start, d.end || now].map(xScale);
          d.x = Math.min(Math.max(a, 0), b); // copied from updatePositions
        })
        .attr('transform', d => `translate(0,${d.y})`);

      const left = xScale(newCenterDate);
      window.scrollTo({left, top: 0, behavior: 'auto' as ScrollBehavior});
    }),
  ).subscribe();

  const sub1 = scrollPos$.pipe(
    throttleTime(100),
    withLatestFrom(windowDim$, ({scrollX}, {innerWidth}) => [scrollX, innerWidth]),
    map(([scrollX, innerWidth]) => [xScale.invert(scrollX), xScale.invert(scrollX + innerWidth)]),
    switchMap(domain => {
      // return worker.getShiftsInRange(domain);
      return getShiftsInRange(domain);
    }),
    tap((data: any) => {
      data.shifts.forEach(updatePositions);
    }),
    map((data, i) => draw(data)),
  ).subscribe();

  const updatePositions = (shift: Shift) => {
    for (const comp of shift.components) {
      const index = comp.type == ShiftComponentType.Projected ? 1 : 0;
      comp.fill = d3.color(shift.employee.color);
      comp.x = xScale(comp.start);
      comp.w = Math.max(xScale(comp.end || new Date()) - comp.x, 0);
    }
    shift.y = shift.row * constants.ROW_STEP + margin.top + axisLabelHeight;
    const [a, b] = [shift.start, shift.end || now].map(xScale);
    shift.x = Math.min(Math.max(a, 0), b);
    if (isNaN(shift.y)) {
      console.log(shift);
    }
    return shift;
  }

  function draw({shifts, employees}: {shifts: Shift[], employees: {[id: string]: Employee}}) {
    svg.attr('width', totalWidth)
      .attr('height', contentHeight);

    gShifts.raise()
      .selectAll('g.shift')
      .data(shifts, d => d['id'])
      .join(enter => enter.append('g').classed('shift', true)
        .call(s => s.append('g').classed('shift-text', true)
          .call(s => s.append('text').classed('name', true))
          .call(s => s.append('g').classed('duration', true).append('text')
            .attr('y', 5)
            .attr('x', 24)
          )
        )
      )
      .attr('transform', d => `translate(0,${d.y})`)
      .call(s => s.select('g.shift-text').attr('transform', d => `translate(${d.x},${-constants.ROW_TEXT_HEIGHT})`)
          .call(s => s.select('text.name').text(d => formatName(d.employee)))
          .each(function (d) {
            const s = d3.select(this);
            const dx = ((this as any).firstChild as SVGGraphicsElement).getBBox().width + 4;
            const frac = d.duration / d.expectedDuration;
            s.select('g.duration')
              .attr('transform', `translate(${dx},0)`)
              .call(drawMiniPie, frac, d.employee.color)
              .call(s => s.select('text').text(formatDuration(d.duration)))
          })
      )
      .call(s => s.selectAll('g.component').data(d => d.components)
        .join(enter => enter.append('g').classed('component', true)
          .call(s => s.append('title'))
          .call(s => s.append('rect')
            .attr('rx', 8)
            .attr('ry', 8)
            .attr('height', constants.RECT_HEIGHT))
        )
        .attr('transform', d => `translate(${d.x},0)`)
        .call(s => s.select('title').text(d => `${formatTime(d.start)}-${formatTime(d.end || new Date())}`))
        .call(s => s.select('rect')
          .attr('width', d => d.w)
          .attr('fill', d => d.fill.toString())
        )
        .each(filterShiftComponentTimeVisibility)
      )
      .on('click', d => {
        cleanup();
        byEmployee(d.employee.id, d.start);
      });
  }

  const onBeforeUnload = () => window.scrollTo(args);
  window.addEventListener('beforeunload', onBeforeUnload);

  let args = {left: xScale(now) - width / 2, top: 0, behavior: 'auto' as ScrollBehavior};
  window.scrollTo(args);

  const cleanup = () => {
    svg.select('g.axis.top').remove()
    svg.select('g.axis.bottom').remove()
    svg.select('g.axis.date').remove()
    window.removeEventListener('beforeunload', onBeforeUnload);
    sub0.unsubscribe();
    sub1.unsubscribe();
  };

  function drawTopAxis(sel, scale, h) {
    if (sel.select('g.axis.top').empty()) {
      sel.append('g').classed('axis top', true);
    }
    return sel.select('g.axis.top')
      .attr('transform', `translate(0,${margin.top})`)
      .call(d3.axisTop(scale).ticks(d3.timeHour.every(3)))
      .call(s => s.select('path').remove())
      .call(s => s.selectAll('.tick').select('line')
        .attr('y2', h)
      );
  }
  
  function drawBottomAxis(sel, scale, h) {
    if (sel.select('g.axis.bottom').empty()) {
      sel.append('g').classed('axis bottom', true);
    }
  
    return sel.select('g.axis.bottom')
      .attr('transform', `translate(0,${h})`)
      .call(d3.axisBottom(scale).ticks(d3.timeHour.every(3)))
      .call(s => s.select('path').remove())
      .call(s => s.selectAll('.tick').select('line').remove());
  }
  
  function drawDateAxis(sel, scale) {
    if (sel.select('g.axis.date').empty()) {
      sel.append('g').classed('axis date', true);
    }
    const days = [];
    const [minDate, maxDate] = scale.domain();
    let d = d3.timeDay.floor(minDate)
    while (d < maxDate) {
      days.push(d3.timeHour.offset(d, 12));
      d = d3.timeDay.offset(d, 1)
    }
    sel.select('g.axis.date').attr('transform', `translate(0,${48})`).selectAll('g.label').data(days, d => d.toISOString().slice(0, 10)).join(
      enter => enter.append('g').classed('label', true)
        .attr('transform', d => `translate(${scale(d)},0)`)
        .call(s => s.append('text')
          .attr('text-anchor', 'middle')
          .attr('alignment-baseline', 'middle')
          .text(d => formatDateWeekday(d))
        ),
      update => update.attr('transform', d => `translate(${scale(d)},0)`),
      exit => exit.remove(),
    );
  }
}

function byEmployee(employeeId: string, date = new Date()) {
  const margin = {top: 80, left: 80, right: 10, bottom: 10};
  const {innerWidth: width, innerHeight: height} = window;

  const domainHeight = height / constants.ROW_STEP * 8.64e7;
  const d0 = new Date(+date - domainHeight / 2);
  const d1 = new Date(+date + domainHeight / 2);

  const totalHeight = constants.ROW_STEP * 20 * 7;

  const yScale = d3.scaleTime().domain([d0, d1]).range([totalHeight / 2 - height / 2, totalHeight / 2 + height / 2]);
  const xScale = d3.scaleTime().range([margin.left, width - margin.right]);

  const fullRange = [0, totalHeight];
  const fullDomain = [yScale.invert(0), yScale.invert(totalHeight)];
  yScale.range(fullRange).domain(fullDomain);

  svg.attr('width', width).attr('height', totalHeight);
  gShifts.raise();

  const axisData = [
    {classed: ['left'], id: 'employee-axis-left', transform: [64, 0], fn: d3.axisLeft(yScale).ticks(d3.timeWeek.every(1))},
  ];

  svg.selectAll('g.axis').data(axisData, d => d['id'])
    .join(
      enter => enter.append('g')
        .attr('class', d => ['axis', ...d.classed].join(' ')),
    )
    .attr('transform', d => `translate(${d.transform.join(',')})`)
    .call(d => d['fn'])

  const onReset = () => {
    window.scrollTo({...args, top: yScale(d3.timeWeek.floor(now)), behavior: 'smooth'});
  }

  const scrollPos = fromEvent(window, 'scroll').pipe(
    map(() => [window.scrollY, window.innerHeight]),
    share(),
  );

  const sub = scrollPos.pipe(
    throttleTime(100),
    map(([y0, h]) => [yScale.invert(y0), yScale.invert(y0 + h)]),
    switchMap(domain => worker.getShiftsByEmployeeInRange(employeeId, domain)),
    map((data: any, index) => {
      const [minTime, maxTime] = d3.extent(data.shifts
        .reduce((acc, s) => {
          calculateNorms(s);
          for (const comp of s.components) {
            acc.push(comp.startNorm);
            acc.push(comp.endNorm);
          }
          return acc;
        }, [] as Date[])) as [Date, Date];
      xScale.domain([minTime, maxTime]);

      data.shifts.forEach(updatePositions);
      draw(data);
    }),
  ).subscribe()

  const updatePositions = (shift: Shift) => {
    calculateNorms(shift);
    for (const comp of shift.components) {
      const index = comp.type == ShiftComponentType.Projected ? 1 : 0;
      comp.fill = d3.color(shift.employee.color);
      comp.x = xScale(comp.startNorm);
      comp.w = Math.max(xScale(comp.endNorm) - comp.x, 0);
    }
    shift.y = yScale(d3.timeDay.floor(shift.start));
    shift.x = Math.max(xScale(shift.startNorm), 0);
    return shift;
  }

  function draw({shifts, employees}: {shifts: Shift[], employees: {[id: string]: Employee}}) {
    gShifts.selectAll('g.shift')
      .data(shifts, d => d['id'])
      .join(enter => enter.append('g').classed('shift', true)
        .call(s => s.append('g').classed('shift-text', true)
          .call(s => s.append('text').classed('name', true))
          .call(s => s.append('g').classed('duration', true).append('text')
            .attr('y', 5)
            .attr('x', 24)
          )
        )
      )
      .attr('transform', d => `translate(0,${d.y})`)
      .each(function (d) {
        const s = d3.select(this);
        s.select('g.shift-text')
          .attr('transform', `translate(${d.x},${-constants.ROW_TEXT_HEIGHT})`)
          .call(s => s.select('text.name').text(formatDateWeekday(d.start)))
          .call(s => {
            const dx = s.select<SVGGraphicsElement>('g.shift-text > .name').node().getBBox().width + 4;
            s.select('g.duration')
              .attr('transform', `translate(${dx},0)`)
              .call(drawMiniPie, d.duration / d.expectedDuration, d.employee.color)
              .call(s => s.select('text').text(formatDuration(d.duration)))
          });
      })
      .call(s => s.selectAll('g.component').data(d => d.components).join(
        enter => enter.append('g').classed('component', true)
          .call(s => s.append('title'))
          .call(s => s.append('rect')
            .attr('rx', 8)
            .attr('ry', 8)
            .attr('height', constants.RECT_HEIGHT)
          ),
        )
        .attr('transform', d => `translate(${d.x},0)`)
        .call(s => s.select('title').text(d => `${formatTime(d.start)}-${formatTime(d.end || new Date())}`))
        .call(s => s.select('rect').attr('width', d => d.w).attr('fill', d => d.fill.toString()))
        .each(filterShiftComponentTimeVisibility)
      )
      .on('click', d => {
        cleanup();
        byTime(d.start);
      });
  }

  const onBeforeUnload = () => window.scrollTo(args);

  window.addEventListener('beforeunload', onBeforeUnload);

  let args = {left: 0, top: yScale(d3.timeWeek.floor(date)), behavior: 'auto' as ScrollBehavior};
  window.scrollTo(args);

  const cleanup = () => {
    svg.select('g.axis.left').remove();
    window.removeEventListener('beforeunload', onBeforeUnload);
    sub.unsubscribe();
  };
}


function calculateNorms(shift: Shift) {
  let date, offset;
  const first = shift.components.length > 0 ? shift.components[0].start : null;
  date = new Date(shift.start);
  date.setFullYear(2000);
  date.setMonth(0);
  date.setDate(1);
  shift.startNorm = date;
  for (const comp of shift.components) {
    date = new Date(comp.start);
    offset = d3.timeDay.count(first, date);
    date.setFullYear(2000);
    date.setMonth(0)
    date.setDate(1 + offset);
    comp.startNorm = date;

    date = new Date(comp.end || new Date());
    offset = d3.timeDay.count(first, date);
    date.setFullYear(2000);
    date.setMonth(0)
    date.setDate(1 + offset);
    comp.endNorm = date;
  }
}

const arc = d3.arc();
function drawMiniPie(s, frac: number, employeeColor: string, radius = 10) {
  const endAngle = 2 * Math.PI * Math.min(Math.max(frac, 0), 1);
  const startAngle = 0;
  if (s.select('g.pie').empty()) {
    s = s.append('g').classed('pie', true).attr('transform', `translate(${radius},0)`)
      .call(s => s.append('circle')
        .attr('r', 10)
        .attr('fill', d3.hsl(employeeColor).brighter(0.9)))
      .call(s => s.append('path').attr('fill', employeeColor))
  }
  return s.call(s => s.select('path').attr('d', arc({ startAngle, endAngle, outerRadius: radius, innerRadius: 0 })))
}

function filterShiftComponentTimeVisibility (d: ShiftComponent) {
  const data = [
    {'class': 'start', x: 4, 'opacity': d.w > constants.START_MIN_WIDTH_THRESHOLD ? 1 : 0, text: formatTime(d.start)},
    {'class': 'end', x: d.w - 4, 'opacity': d.w > constants.END_MIN_WIDTH_THRESHOLD ? 1 : 0, text: formatTime(d.end || new Date())},
  ];
  return d3.select(this).selectAll('text.time').data(data, d => d['class']).join(
    enter => enter.append('text').attr('class', d => `${d['class']} time`).attr('y', constants.RECT_HEIGHT / 2),
    update => update,
    exit => exit.remove()
  )
    .attr('x', d => d.x)
    .attr('opacity', d => d.opacity)
    .text(d => d.text);
}

const formatName = (empl: Employee) => `${empl.first_name} ${empl.last_name}`;
const formatTransform = ([x, y]: [number, number]) => {
  if (isNaN(y)) {
    throw new Error('wahtt');
  }
  return `translate(${x},${y})`;
}
const data = {
  shifts: [],
  employeeIds: [],
  employees: {},
};


const smooth = false;
document.addEventListener('DOMContentLoaded', async () => {
  await main();
});


const SOCKET_URL = `ws://${window.location.host}/socket`;
socket(SOCKET_URL, undefined, true, (data) => {
  console.log('data', data);
  return data as any;
}).pipe(switchMap(v => v)).subscribe(val => console.log(val));
