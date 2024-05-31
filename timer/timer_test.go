// Copyright 2024 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package timer_test

import (
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"

	"github.com/imoore76/go-ldlm/timer"
)

func TestManager(t *testing.T) {
	assert := assert.New(t)
	expired := newSafeStringSlice()
	m, cl := timer.NewManager()

	m.Add("foo", func() {
		expired.Add("foo")
	}, 1*time.Millisecond)
	m.Add("me", func() {
		expired.Add("me")
	}, 10*time.Millisecond)
	m.Add("baz", func() {
		expired.Add("baz")
	}, 1*time.Hour)
	time.Sleep(100 * time.Millisecond)
	cl()

	assert.Equal([]string{"foo", "me"}, expired.Get())
}

func TestManager_Refresh(t *testing.T) {
	assert := assert.New(t)
	expired := newSafeStringSlice()
	m, cl := timer.NewManager()

	m.Add("foo", func() {
		expired.Add("foo")
	}, 1*time.Minute)
	m.Add("me", func() {
		expired.Add("me")
	}, 1*time.Second)
	m.Add("baz", func() {
		expired.Add("baz")
	}, 1*time.Hour)

	ok, err := m.Refresh("me", 1*time.Hour)
	assert.Nil(err)
	assert.True(ok)

	time.Sleep(1500 * time.Millisecond)
	cl()

	// Nothing has expired
	assert.Equal([]string{}, expired.Get())
}

func TestManager_Remove(t *testing.T) {
	assert := assert.New(t)
	expired := newSafeStringSlice()
	m, cl := timer.NewManager()

	m.Add("foo", func() {
		expired.Add("foo")
	}, 1*time.Hour)
	m.Add("me", func() {
		expired.Add("me")
	}, 1*time.Second)
	m.Add("baz", func() {
		expired.Add("baz")
	}, 1*time.Hour)

	m.Remove("me")
	time.Sleep(1500 * time.Millisecond)
	cl()

	// me:you should have been removed before it expired
	assert.Equal([]string{}, expired.Get())

	m.Remove("not") // should do nothing
}

func TestManager_Shutdown(t *testing.T) {
	assert := assert.New(t)
	expired := newSafeStringSlice()
	m, cl := timer.NewManager()
	defer cl()

	m.Add("foo", func() {
		expired.Add("foo")
	}, 1*time.Second)
	m.Add("me", func() {
		expired.Add("me")
	}, 1*time.Second)
	m.Add("baz", func() {
		expired.Add("baz")
	}, 1*time.Second)

	cl()
	time.Sleep(1500 * time.Millisecond)

	// Nothing has expired because timers were stopped
	assert.Equal([]string{}, expired.Get())
}

type safeStringSlice struct {
	sync.Mutex
	s []string
}

func (ss *safeStringSlice) Add(s string) {
	ss.Lock()
	defer ss.Unlock()
	ss.s = append(ss.s, s)
}

func (ss *safeStringSlice) Get() []string {
	ss.Lock()
	defer ss.Unlock()
	return ss.s
}

func newSafeStringSlice() *safeStringSlice {
	return &safeStringSlice{
		s: []string{},
	}
}
